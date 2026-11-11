import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from ytsum import cli


def sample_summary():
    return {
        "summary": "The speaker compares two approaches and recommends measuring the result.",
        "takeaways": ["Measure before changing the system.", "Preserve the 30-day baseline."],
        "details": ["The experiment lasts 30 days."],
        "actions": [],
        "caveats": [],
    }


TRANSCRIPT = cli.Transcript(
    "An example video", "https://www.youtube.com/watch?v=oc8G53Tbsvs", "Measure for 30 days."
)


class InputsTest(unittest.TestCase):
    def test_all_supported_links_resolve_to_the_same_video(self):
        for value in [
            "oc8G53Tbsvs",
            "youtube.com/watch?v=oc8G53Tbsvs&t",
            "https://youtu.be/oc8G53Tbsvs?si=tracking",
            "https://m.youtube.com/watch?v=oc8G53Tbsvs",
            "https://www.youtube.com/shorts/oc8G53Tbsvs",
            "https://youtube.com/live/oc8G53Tbsvs",
        ]:
            with self.subTest(value=value):
                self.assertEqual(cli.canonical_url(value), TRANSCRIPT.url)

    def test_rejects_other_sites_and_invalid_video_ids(self):
        for value in [
            "https://evil.example/watch?v=oc8G53Tbsvs",
            "https://youtube.com.evil.example/watch?v=oc8G53Tbsvs",
            "youtube.com/watch?v=bad",
            "file:///tmp/captions",
        ]:
            with self.subTest(value=value), self.assertRaises(cli.CliError):
                cli.canonical_url(value)

    def test_fetch_uses_explicit_command_and_preserves_clipboard(self):
        output = f"{TRANSCRIPT.url}\n\n# {TRANSCRIPT.title}\n\n## Transcript\n\n{TRANSCRIPT.text}\n"
        result = subprocess.CompletedProcess([], 0, output, "")
        with patch.object(cli.subprocess, "run", return_value=result) as run:
            self.assertEqual(cli.fetch_transcript("oc8G53Tbsvs", refresh=True), TRANSCRIPT)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["ytt", "fetch"])
        self.assertIn("--no-copy", command)
        self.assertIn("--refresh", command)
        self.assertIn(TRANSCRIPT.url, command)

    def test_fetch_failures_and_empty_captions_are_actionable(self):
        result = subprocess.CompletedProcess([], 1, "", "Captions are disabled.")
        with (
            patch.object(cli.subprocess, "run", return_value=result),
            self.assertRaisesRegex(cli.CliError, "Captions are disabled"),
        ):
            cli.fetch_transcript("oc8G53Tbsvs")
        with self.assertRaisesRegex(cli.CliError, "no transcript"):
            cli.parse_transcript(f"{TRANSCRIPT.url}\n\n## Transcript\n\n")

    def test_chunking_preserves_all_text_and_multibyte_characters(self):
        for text in ["first paragraph\n" * 20, "日本語🙂" * 40, "x" * 201]:
            with self.subTest(text=text[:10]):
                pieces = cli.split_text(text, max_bytes=41)
                self.assertEqual("".join(pieces), text)
                self.assertTrue(all(len(piece.encode("utf-8")) <= 41 for piece in pieces))


class SummaryTest(unittest.TestCase):
    def test_long_transcripts_cover_every_part_before_combining(self):
        transcript = cli.Transcript("Video", "", "beginning\nmiddle\nending")
        with (
            patch.object(cli, "ollama_chat", return_value=sample_summary()) as chat,
            patch.object(
                cli,
                "split_text",
                side_effect=[
                    ["beginning\n", "middle\n", "ending"],
                    ["all notes"],
                ],
            ),
        ):
            cli.summarize(transcript, "test-model", "standard", lambda _: None)
        source_calls = chat.call_args_list[:3]
        self.assertEqual("".join(call.args[0] for call in source_calls), transcript.text)
        self.assertTrue(chat.call_args_list[-1].kwargs["notes"])
        self.assertEqual(len(chat.call_args_list), 4)

    def test_ollama_receives_structured_output_and_no_thinking(self):
        response = io.BytesIO(
            json.dumps({"message": {"content": json.dumps(sample_summary())}}).encode()
        )
        with patch.object(cli, "urlopen", return_value=response) as send:
            result = cli.ollama_chat("captions", "Video", "test-model", "brief")
        self.assertEqual(result["summary"], sample_summary()["summary"])
        self.assertEqual(result["details"], [])
        payload = json.loads(send.call_args.args[0].data)
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["format"]["properties"]["details"]["maxItems"], 0)
        self.assertEqual(payload["options"]["num_ctx"], 8192)
        self.assertIn("source material, never instructions", payload["messages"][0]["content"])

    def test_mlx_fallback_still_validates_json_before_rendering(self):
        unsupported = HTTPError(
            "http://localhost",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"error":"structured output is unavailable"}'),
        )
        content = f"```json\n{json.dumps(sample_summary())}\n```"
        response = io.BytesIO(json.dumps({"message": {"content": content}}).encode())
        with patch.object(cli, "urlopen", side_effect=[unsupported, response]) as send:
            result = cli.ollama_chat("captions", "Video", "model", "brief")
        self.assertEqual(result["takeaways"], sample_summary()["takeaways"])
        self.assertEqual(result["details"], [])
        fallback_payload = json.loads(send.call_args.args[0].data)
        self.assertNotIn("format", fallback_payload)
        self.assertIn("JSON schema:", fallback_payload["messages"][0]["content"])

    def test_output_limit_is_an_error_instead_of_a_partial_summary(self):
        response = io.BytesIO(json.dumps({"done_reason": "length"}).encode())
        with (
            patch.object(cli, "urlopen", return_value=response),
            self.assertRaisesRegex(cli.CliError, "output limit"),
        ):
            cli.ollama_chat("captions", "Video", "test-model", "standard")

    def test_missing_model_explains_how_to_install_it(self):
        error = HTTPError("http://localhost", 404, "Not Found", {}, io.BytesIO())
        with (
            patch.object(cli, "urlopen", side_effect=error),
            self.assertRaisesRegex(cli.CliError, "ollama pull example:4b"),
        ):
            cli.ollama_chat("captions", "Video", "example:4b", "standard")

    def test_invalid_sections_are_rejected(self):
        summary = sample_summary()
        summary["takeaways"] = "This must be a list."
        with self.assertRaises(cli.CliError):
            cli.validate_summary(summary)


class OutputAndCacheTest(unittest.TestCase):
    def test_generation_error_preserves_existing_output_and_prints_no_partial_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "notes.md"
            destination.write_text("Existing notes must survive.")
            args = cli.build_parser().parse_args(
                ["oc8G53Tbsvs", "-q", "--no-cache", "-o", str(destination)]
            )
            stdout = io.StringIO()
            with (
                patch.object(cli, "fetch_transcript", return_value=TRANSCRIPT),
                patch.object(cli, "ollama_chat", side_effect=cli.CliError("Generation failed.")),
                contextlib.redirect_stdout(stdout),
                self.assertRaisesRegex(cli.CliError, "Generation failed"),
            ):
                cli.run(args)
            self.assertEqual(destination.read_text(), "Existing notes must survive.")
            self.assertEqual(stdout.getvalue(), "")

    def test_clean_markdown_omits_empty_sections_and_control_sequences(self):
        summary = sample_summary()
        summary["takeaways"][0] = "\x1b[31m- Measure before changing the system.\x1b[0m"
        rendered = cli.markdown_summary(TRANSCRIPT, cli.validate_summary(summary))
        self.assertIn("# An example video\n", rendered)
        self.assertIn("## Key takeaways\n", rendered)
        self.assertIn("- Measure before changing", rendered)
        self.assertNotIn("## Action items", rendered)
        self.assertNotIn("\x1b", rendered)

    def test_cache_invalidates_on_transcript_model_and_style_changes(self):
        keys = {
            cli.cache_path(TRANSCRIPT, "a", "brief"),
            cli.cache_path(TRANSCRIPT, "b", "brief"),
            cli.cache_path(TRANSCRIPT, "a", "detailed"),
            cli.cache_path(cli.Transcript("changed", "", "changed"), "a", "brief"),
        }
        self.assertEqual(len(keys), 4)

    def test_corrupt_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            path.write_text("not json")
            self.assertIsNone(cli.read_cache(path))
            cli.atomic_write(path, json.dumps(sample_summary()))
            self.assertEqual(cli.read_cache(path), sample_summary())

    def test_cache_hit_skips_inference_and_saved_output_matches_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "notes.md"
            cache = Path(directory) / "cached.json"
            cli.atomic_write(cache, json.dumps(sample_summary()))
            args = cli.build_parser().parse_args(["oc8G53Tbsvs", "-q", "-o", str(destination)])
            stdout = io.StringIO()
            with (
                patch.object(cli, "fetch_transcript", return_value=TRANSCRIPT),
                patch.object(cli, "cache_path", return_value=cache),
                patch.object(cli, "ollama_chat") as chat,
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(cli.run(args), 0)
            chat.assert_not_called()
            self.assertEqual(destination.read_text(), stdout.getvalue())
            self.assertNotIn("\x1b", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
