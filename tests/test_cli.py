import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import ANY, Mock, patch

from rich.console import Console

from ytsum import cli
from ytsum.models import CliError, Cue, Transcript

VIDEO = "https://www.youtube.com/watch?v=20CgG5pUv9Q"
OTHER = "https://www.youtube.com/watch?v=kCc8FmEb1nY"
THIRD = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
SOURCE = Transcript(
    "Video",
    VIDEO,
    "Measure latency first.",
    (Cue("Measure latency first.", 65, 70),),
    "captions",
    "en",
)
SUMMARY = {
    "summary": "Measure latency.",
    "takeaways": [{"text": "Measure first.", "source_ids": ["S1"]}],
    "details": [],
    "actions": [],
    "caveats": [],
}


class CliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        environment = patch.dict("os.environ", {"XDG_CACHE_HOME": str(self.root / "cache")})
        environment.start()
        self.addCleanup(environment.stop)
        self.stderr = Console(file=io.StringIO())

    def execute(self, *arguments):
        args = cli.build_parser().parse_args(list(arguments))
        return cli.execute(args, Mock(), self.stderr)

    def test_cache_hit_skips_inference_and_saved_output_matches_stdout(self):
        output = self.root / "notes.md"
        with (
            patch.object(cli, "acquire", return_value=SOURCE),
            patch.object(cli.summaries, "summarize", return_value=SUMMARY) as model,
        ):
            first, _, code = self.execute(VIDEO, "-o", str(output))
            second, footer, _ = self.execute(VIDEO, "-o", str(output))
        self.assertEqual(code, 0)
        self.assertEqual(first, second)
        self.assertEqual(output.read_text(), second)
        self.assertIn("cached", footer)
        model.assert_called_once()

    def test_generation_failure_preserves_existing_output_and_no_partial_stdout(self):
        output = self.root / "notes.md"
        output.write_text("Keep these notes.")
        stdout = io.StringIO()
        with (
            patch("sys.argv", ["ytsum", VIDEO, "-o", str(output), "-q"]),
            patch.object(cli, "acquire", return_value=SOURCE),
            patch.object(cli.summaries, "summarize", side_effect=CliError("generation failed")),
            redirect_stdout(stdout),
            redirect_stderr(io.StringIO()),
        ):
            code = cli.main()
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(output.read_text(), "Keep these notes.")

    def test_batch_continues_after_failure_and_preserves_previous_note(self):
        old = self.root / "kCc8FmEb1nY.md"
        old.write_text("Previous successful summary")
        third = Transcript("Third", THIRD, SOURCE.text, SOURCE.cues, "captions")
        with (
            patch.object(cli, "acquire", side_effect=[SOURCE, CliError("Video is private"), third]),
            patch.object(cli, "generate", return_value=("# Saved notes\n", False)),
        ):
            index, _, code = self.execute(VIDEO, OTHER, THIRD, "-d", str(self.root))
        self.assertEqual(code, 1)
        self.assertTrue((self.root / "20CgG5pUv9Q.md").is_file())
        self.assertTrue((self.root / "jNQXAC9IVRw.md").is_file())
        self.assertEqual(old.read_text(), "Previous successful summary")
        self.assertEqual(index, (self.root / "index.md").read_text())
        self.assertIn("2 saved · 1 failed", index)
        self.assertIn("Video is private", index)

    def test_batch_file_and_stdin_read_urls_and_deduplicate(self):
        batch = self.root / "urls.txt"
        batch.write_text(f"# My list\n\n{VIDEO}\n{VIDEO}\n")
        for filename in (str(batch), "-"):
            with (
                self.subTest(filename=filename),
                patch("sys.stdin", io.StringIO(batch.read_text())),
                patch.object(cli, "acquire", return_value=SOURCE) as acquire,
                patch.object(cli, "generate", return_value=("Notes\n", True)),
            ):
                _, _, code = self.execute("--batch", filename, "-d", str(self.root))
            self.assertEqual(code, 0)
            acquire.assert_called_once()

    def test_playlist_produces_index_even_with_one_video(self):
        with (
            patch.object(cli.sources, "list_playlist", return_value=[VIDEO]),
            patch.object(cli, "acquire", return_value=SOURCE),
            patch.object(cli, "generate", return_value=("Notes\n", False)),
        ):
            _, _, code = self.execute(
                "youtube.com/playlist?list=PLexample", "--limit", "1", "-d", str(self.root)
            )
        self.assertEqual(code, 0)
        self.assertTrue((self.root / "index.md").is_file())

    def test_invalid_batch_encoding_has_an_actionable_error_before_inference(self):
        batch = self.root / "bad.txt"
        batch.write_bytes(b"\xff\xfe")
        with patch.object(cli, "acquire") as acquire, self.assertRaisesRegex(CliError, "UTF-8"):
            self.execute("--batch", str(batch))
        acquire.assert_not_called()

    def test_clipboard_uses_same_normalization_and_explicit_empty_does_not_read_it(self):
        with (
            patch.object(
                cli, "clipboard_url", return_value=r"https://youtu.be/20CgG5pUv9Q"
            ) as clipboard,
            patch.object(cli, "acquire", return_value=SOURCE) as acquire,
            patch.object(cli, "generate", return_value=("Notes", False)),
        ):
            self.execute()
            acquire.assert_called_once_with(VIDEO, ANY, ANY)
            clipboard.assert_called_once()
        with patch.object(cli, "clipboard_url") as clipboard, self.assertRaises(CliError):
            self.execute("")
        clipboard.assert_not_called()

    def test_batch_rejects_single_output_before_inference(self):
        with (
            patch.object(cli, "acquire") as acquire,
            self.assertRaisesRegex(CliError, "output-dir"),
        ):
            self.execute(VIDEO, OTHER, "-o", str(self.root / "notes.md"))
        acquire.assert_not_called()

    def test_transcript_input_is_untimed_and_rejects_empty_or_incompatible_flags(self):
        with (
            patch("sys.stdin", io.StringIO("Measure latency first.")),
            patch.object(cli.summaries, "summarize", return_value=SUMMARY),
        ):
            output, _, code = self.execute("-")
        self.assertEqual(code, 0)
        self.assertNotIn("&t=", output)
        for arguments in (("-",), ("-", VIDEO), ("-", "--batch", "-"), ("-", "-d", str(self.root))):
            with (
                self.subTest(arguments=arguments),
                patch("sys.stdin", io.StringIO("")),
                patch.object(cli, "generate") as generate,
                self.assertRaises(CliError),
            ):
                self.execute(*arguments)
            generate.assert_not_called()

    def test_malformed_url_returns_one_without_traceback(self):
        stderr = io.StringIO()
        with (
            patch("sys.argv", ["ytsum", "https://[youtube.com/watch?v=20CgG5pUv9Q", "-q"]),
            redirect_stderr(stderr),
        ):
            self.assertEqual(cli.main(), 1)
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_interrupt_preserves_completed_notes_and_index(self):
        with (
            patch.object(cli, "acquire", side_effect=[SOURCE, KeyboardInterrupt()]),
            patch.object(cli, "generate", return_value=("Notes\n", False)),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.execute(VIDEO, OTHER, "-d", str(self.root))
        self.assertEqual((self.root / "20CgG5pUv9Q.md").read_text(), "Notes\n")
        self.assertIn("1 saved · 0 failed", (self.root / "index.md").read_text())
