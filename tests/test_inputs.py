import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ytsum import inputs
from ytsum.models import CliError, Cue, Transcript

VIDEO = "https://www.youtube.com/watch?v=20CgG5pUv9Q"
OTHER = "https://www.youtube.com/watch?v=kCc8FmEb1nY"


class InputTests(unittest.TestCase):
    def test_copied_video_formats_keep_working(self):
        for value in [
            "20CgG5pUv9Q",
            "youtube.com/watch?v=20CgG5pUv9Q&t",
            r"https://www.youtube.com/watch\?v\=20CgG5pUv9Q",
            r"https:\/\/www.youtube.com\/watch\?v\=20CgG5pUv9Q\&t\=10",
            r"\"https://www.youtube.com/watch\?v\=20CgG5pUv9Q\"",
            ' "https://www.youtube.com/watch?v=20CgG5pUv9Q" ',
            "‘https://youtu.be/20CgG5pUv9Q’",
            "“https://youtu.be/20CgG5pUv9Q”",
            "`https://youtu.be/20CgG5pUv9Q`",
            "<https://youtu.be/20CgG5pUv9Q>",
            "[Watch](https://youtu.be/20CgG5pUv9Q)",
            "[Watch](<https://youtu.be/20CgG5pUv9Q>)",
            "https://youtube.com/watch/?v=20CgG5pUv9Q",
            "https://youtube.com/watch?feature=shared&amp;v=20CgG5pUv9Q",
            "https://youtube.com/watch%3Fv%3D20CgG5pUv9Q%26t%3D30",
            "https%3A%2F%2Fyoutu.be%2F20CgG5pUv9Q",
            "\ufeffhttps://youtu.be/20CgG5pUv9Q\u200b",
            "https://youtu.be/20Cg\u200bG5pUv9Q",
            "https://m.youtube.com/watch?v=20CgG5pUv9Q",
            "https://youtube.com/shorts/20CgG5pUv9Q",
            "https://youtube.com/live/20CgG5pUv9Q",
        ]:
            with self.subTest(value=value):
                self.assertEqual(inputs.canonical_url(value), VIDEO)

    def test_rejects_unrelated_sites_and_malformed_links(self):
        for value in [
            "https://evil.example/watch?v=20CgG5pUv9Q",
            "https://youtube.com.evil.example/watch?v=20CgG5pUv9Q",
            "[Watch](https://evil.example/watch?v=20CgG5pUv9Q)",
            "https%3A%2F%2Fevil.example%2Fwatch%3Fv%3D20CgG5pUv9Q",
            "https://evil.example/?next=https://youtube.com/watch?v=20CgG5pUv9Q",
            "$(echo https://youtu.be/20CgG5pUv9Q)",
            "file:///tmp/captions",
            "https://[youtube.com/watch?v=20CgG5pUv9Q",
            "youtube.com/watch?v=bad",
            "",
        ]:
            with self.subTest(value=value), self.assertRaises(CliError):
                inputs.canonical_url(value)

    def test_watch_links_do_not_expand_playlists_without_opt_in(self):
        url = VIDEO + "&list=PLexample"
        self.assertIsNone(inputs.playlist_url(url))
        self.assertEqual(
            inputs.playlist_url(url, force=True), "https://www.youtube.com/playlist?list=PLexample"
        )
        self.assertEqual(
            inputs.playlist_url("youtube.com/playlist?list=PLexample"),
            "https://www.youtube.com/playlist?list=PLexample",
        )

    def test_playlist_resolution_preserves_order_and_deduplicates(self):
        expand = Mock(return_value=[VIDEO, OTHER, VIDEO])
        targets, batch = inputs.resolve_targets(
            ["youtube.com/playlist?list=PLexample", OTHER], expand=expand
        )
        self.assertTrue(batch)
        self.assertEqual([item.url for item in targets], [VIDEO, OTHER])

    def test_limit_applies_after_deduplication(self):
        targets, batch = inputs.resolve_targets([VIDEO, VIDEO, OTHER], expand=Mock(), limit=2)
        self.assertTrue(batch)
        self.assertEqual([item.url for item in targets], [VIDEO, OTHER])

    def test_bad_batch_entries_are_results_not_fatal_resolution_errors(self):
        targets, batch = inputs.resolve_targets([VIDEO, "not a video", OTHER], expand=Mock())
        self.assertTrue(batch)
        self.assertTrue(targets[1].error)
        self.assertFalse(targets[0].error or targets[2].error)

    def test_batch_files_skip_comments_and_blank_lines(self):
        self.assertEqual(
            inputs.batch_lines(f"# course\n{VIDEO}\n\n  # comment\n{OTHER}\n"), [VIDEO, OTHER]
        )

    def test_clipboard_is_read_without_writing(self):
        with (
            patch.object(inputs.pyperclip, "paste", return_value=VIDEO),
            patch.object(inputs.pyperclip, "copy") as copy,
        ):
            self.assertEqual(inputs.clipboard_url(), VIDEO)
        copy.assert_not_called()

    def test_clipboard_failures_are_actionable(self):
        with (
            patch.object(inputs.pyperclip, "paste", return_value=" "),
            self.assertRaisesRegex(CliError, "empty"),
        ):
            inputs.clipboard_url()
        with (
            patch.object(
                inputs.pyperclip, "paste", side_effect=inputs.pyperclip.PyperclipException()
            ),
            self.assertRaisesRegex(CliError, "Pass a YouTube link"),
        ):
            inputs.clipboard_url()

    def test_explicit_language_preferences_override_legacy_settings(self):
        with patch.dict("os.environ", {"YTSUM_LANGUAGES": "fr,en"}):
            self.assertEqual(inputs.preferred_languages(), ("fr", "en"))
            self.assertEqual(inputs.preferred_languages("de,en,de"), ("de", "en"))
        with self.assertRaises(CliError):
            inputs.preferred_languages("../../invalid")

    def test_legacy_ytt_language_preferences_are_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ytt").mkdir()
            (root / "ytt/config.json").write_text('{"preferred_languages":["fr","en"]}')
            with (
                patch.dict("os.environ", {"XDG_CONFIG_HOME": directory}, clear=True),
                patch("sys.platform", "linux"),
            ):
                self.assertEqual(inputs.preferred_languages(), ("fr", "en"))


class SourceDataTests(unittest.TestCase):
    def test_invalid_source_timestamps_are_rejected(self):
        for start, end in [
            (-1, 2),
            (float("nan"), 2),
            (1, float("inf")),
            (True, 2),
            (2, 1),
            (None, 1),
        ]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                Cue("words", start, end)

    def test_blocks_keep_every_word_and_actual_start_times(self):
        cues = tuple(
            Cue(f"part{index} " + "words " * 40, index * 30, index * 30 + 20) for index in range(5)
        )
        transcript = Transcript(
            "Test", VIDEO, "\n".join(cue.text for cue in cues), cues, "captions"
        )
        blocks = transcript.blocks()
        self.assertEqual(" ".join(block.text for block in blocks).split(), transcript.text.split())
        self.assertEqual([block.start for block in blocks], [0, 30, 60, 90, 120])
        self.assertEqual([block.id for block in blocks], ["S1", "S2", "S3", "S4", "S5"])

    def test_plain_text_never_gets_invented_timestamps(self):
        self.assertTrue(
            all(block.start is None for block in Transcript("Text", "", "words " * 500).blocks())
        )

    def test_transcript_cache_round_trip_preserves_timings(self):
        import json

        original = Transcript("Test", VIDEO, "Words", (Cue("Words", 1.5, 3.0),), "captions", "en")
        self.assertEqual(Transcript.from_data(json.loads(original.to_json())), original)
