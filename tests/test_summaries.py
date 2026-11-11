import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from ytsum import summaries
from ytsum.models import CliError, Cue, Transcript

VIDEO = "https://www.youtube.com/watch?v=20CgG5pUv9Q"
SOURCE = Transcript(
    "Example video",
    VIDEO,
    "Measure latency before changing the system.",
    (Cue("Measure latency before changing the system.", 65.8, 75.0),),
    "captions",
    "en",
)


def sample_summary(ids=("S1",)):
    return {
        "summary": "The speaker recommends measuring latency first.",
        "takeaways": [{"text": "Measure before changing the system.", "source_ids": list(ids)}],
        "details": [],
        "actions": [],
        "caveats": [],
    }


class EvidenceTests(unittest.TestCase):
    def test_shared_source_excerpt_is_shown_once(self):
        value = sample_summary()
        value["takeaways"].append({"text": "Another point.", "source_ids": ["S1"]})
        output = summaries.markdown_summary(SOURCE, value)
        self.assertEqual(output.count("  > Measure latency"), 1)
        self.assertEqual(output.count("&t=65"), 2)

    def test_links_and_excerpts_come_from_source_not_model_fields(self):
        value = sample_summary()
        value["takeaways"][0].update({"timestamp": 99999, "quote": "Invented quote."})
        summary = summaries.validate_summary(value, {"S1"})
        output = summaries.markdown_summary(SOURCE, summary)
        self.assertIn(f"[1:05]({VIDEO}&t=65)", output)
        self.assertIn("> Measure latency before changing the system.", output)
        self.assertNotIn("99999", output)
        self.assertNotIn("Invented quote", output)

    def test_unknown_missing_or_empty_source_ids_are_rejected(self):
        for ids in (["S999"], [], [None], ["S1", "S2", "S3"]):
            value = sample_summary()
            value["takeaways"][0]["source_ids"] = ids
            with self.subTest(ids=ids), self.assertRaises(CliError):
                summaries.validate_summary(value, {"S1"})

    def test_untimed_input_gets_excerpts_without_fake_time_links(self):
        source = Transcript("Transcript", "", SOURCE.text)
        output = summaries.markdown_summary(source, sample_summary())
        self.assertIn("no timestamps available", output)
        self.assertIn("> Measure latency before changing the system.", output)
        self.assertNotIn("&t=", output)

    def test_excerpts_can_be_hidden_without_removing_timestamp_links(self):
        output = summaries.markdown_summary(SOURCE, sample_summary(), excerpts=False)
        self.assertIn("&t=65", output)
        self.assertNotIn("  >", output)

    def test_asr_provenance_and_uncertain_timings_are_visible(self):
        source = Transcript("Video", VIDEO, SOURCE.text, SOURCE.cues, "whisper", "en")
        self.assertIn(
            "timestamps are approximate", summaries.markdown_summary(source, sample_summary())
        )

    def test_source_control_characters_and_markdown_are_not_executed_or_rendered(self):
        cue = Cue("\x1b[31m[click](https://evil.example)\x1b[0m", 1, 2)
        source = Transcript("Video", VIDEO, cue.text, (cue,), "captions")
        output = summaries.markdown_summary(source, sample_summary())
        self.assertNotIn("\x1b", output)
        self.assertIn(r"\[click\]", output)

    def test_timestamp_format_includes_hours_for_long_videos(self):
        self.assertEqual(summaries.timestamp(3723.9), "1:02:03")


class GenerationTests(unittest.TestCase):
    def test_request_constrains_citation_ids_and_disables_thinking(self):
        response = io.BytesIO(
            json.dumps(
                {"done": True, "message": {"content": json.dumps(sample_summary())}}
            ).encode()
        )
        with patch.object(summaries, "urlopen", return_value=response) as send:
            result = summaries.ollama_chat("[S1] source", "Video", "model", "brief", {"S1"})
        self.assertEqual(result, sample_summary())
        payload = json.loads(send.call_args.args[0].data)
        self.assertFalse(payload["think"])
        source_schema = payload["format"]["properties"]["takeaways"]["items"]["properties"][
            "source_ids"
        ]
        self.assertEqual(source_schema["items"]["enum"], ["S1"])
        self.assertEqual(payload["format"]["properties"]["details"]["maxItems"], 0)

    def test_backend_without_schema_support_still_validates_citations(self):
        unsupported = HTTPError(
            "http://localhost",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"error":"structured output is unavailable"}'),
        )
        response = io.BytesIO(
            json.dumps(
                {"message": {"content": f"```json\n{json.dumps(sample_summary())}\n```"}}
            ).encode()
        )
        with patch.object(summaries, "urlopen", side_effect=[unsupported, response]) as send:
            self.assertEqual(
                summaries.ollama_chat("[S1] source", "Video", "model", "brief", {"S1"}),
                sample_summary(),
            )
        self.assertNotIn("format", json.loads(send.call_args.args[0].data))

    def test_partial_or_malformed_model_output_fails_closed(self):
        for value in [
            {"done_reason": "length"},
            {"done": False},
            {"message": {"content": "not json"}},
            {"message": {"content": json.dumps(sample_summary(("S999",)))}},
        ]:
            response = io.BytesIO(json.dumps(value).encode())
            with (
                self.subTest(value=value),
                patch.object(summaries, "urlopen", return_value=response),
                self.assertRaises(CliError),
            ):
                summaries.ollama_chat("[S1] source", "Video", "model", "brief", {"S1"})

    def test_missing_model_explains_how_to_install_it(self):
        error = HTTPError("http://localhost", 404, "Not Found", {}, io.BytesIO())
        with (
            patch.object(summaries, "urlopen", side_effect=error),
            self.assertRaisesRegex(CliError, "ollama pull example:4b"),
        ):
            summaries.ollama_chat("source", "Video", "example:4b", "standard", {"S1"})

    def test_long_transcripts_cover_every_block_and_keep_ids_during_merge(self):
        cues = tuple(
            Cue(f"part{index} " + "words " * 60, index * 30, index * 30 + 25)
            for index in range(400)
        )
        source = Transcript(
            "Long video", VIDEO, "\n".join(cue.text for cue in cues), cues, "captions"
        )

        def model(text, title, name, style, ids, notes=False):
            return sample_summary((sorted(ids)[0],))

        with patch.object(summaries, "ollama_chat", side_effect=model) as chat:
            result = summaries.summarize(source, "model", "standard", lambda _: None)
        direct = [call for call in chat.call_args_list if not call.kwargs.get("notes")]
        self.assertGreater(len(direct), 1)
        self.assertEqual(
            set().union(*(call.args[4] for call in direct)), {block.id for block in source.blocks()}
        )
        self.assertTrue(chat.call_args_list[-1].kwargs["notes"])
        summaries.validate_summary(result, {block.id for block in source.blocks()})

    def test_cache_identity_includes_timings_model_and_style(self):
        different_time = Transcript(
            SOURCE.title, SOURCE.url, SOURCE.text, (Cue(SOURCE.text, 100, 110),), "captions", "en"
        )
        keys = {
            summaries.cache_path(SOURCE, "a", "brief"),
            summaries.cache_path(SOURCE, "b", "brief"),
            summaries.cache_path(SOURCE, "a", "standard"),
            summaries.cache_path(different_time, "a", "brief"),
        }
        self.assertEqual(len(keys), 4)

    def test_corrupt_or_legacy_summary_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            path.write_text("not json")
            self.assertIsNone(summaries.read_cache(path, SOURCE))
            path.write_text(
                json.dumps({**sample_summary(), "takeaways": ["Legacy unsourced point"]})
            )
            self.assertIsNone(summaries.read_cache(path, SOURCE))
