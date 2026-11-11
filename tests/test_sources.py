import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ytsum import sources
from ytsum.models import CliError, Cue, Transcript

VIDEO = "https://www.youtube.com/watch?v=20CgG5pUv9Q"
TIMED = Transcript(
    "Video", VIDEO, "Actual words", (Cue("Actual words", 12.5, 16.0),), "captions", "en"
)


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict("os.environ", {"XDG_CACHE_HOME": self.directory.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_api_captions_keep_timing_and_prefer_requested_manual_tracks(self):
        def track(language, generated, text):
            return SimpleNamespace(
                language_code=language,
                is_generated=generated,
                fetch=lambda: [SimpleNamespace(text=text, start=12.5, duration=3.5)],
            )

        tracks = [
            track("de", False, "unrequested"),
            track("en", True, "automatic"),
            track("fr", False, "manual"),
        ]
        with patch.object(sources, "YouTubeTranscriptApi") as api:
            api.return_value.list.return_value = tracks
            cues, language = sources.captions_from_api(VIDEO, ("en", "fr"))
        self.assertEqual(language, "fr")
        self.assertEqual(cues, (Cue("manual", 12.5, 16.0),))

    def test_json3_parsing_uses_milliseconds_from_the_caption_file(self):
        value = {
            "events": [
                {
                    "tStartMs": 12500,
                    "dDurationMs": 3500,
                    "segs": [{"utf8": "Actual "}, {"utf8": "&amp; words"}],
                }
            ]
        }
        self.assertEqual(sources.parse_json3(value), (Cue("Actual & words", 12.5, 16.0),))

    def test_captions_are_cached_with_timing_and_refresh_refetches(self):
        with (
            patch.object(sources, "captions_from_api", return_value=(TIMED.cues, "en")) as api,
            patch.object(sources, "fetch_title", return_value="Video"),
            patch.object(sources, "audio_transcript") as audio,
        ):
            first = sources.fetch_transcript(VIDEO)
            self.assertEqual(sources.fetch_transcript(VIDEO), first)
            self.assertEqual(api.call_count, 1)
            sources.fetch_transcript(VIDEO, refresh=True)
            self.assertEqual(api.call_count, 2)
        audio.assert_not_called()

    def test_corrupt_timed_cache_is_refetched(self):
        path = sources.transcript_cache_path(VIDEO, ("en",), "auto", "base")
        path.parent.mkdir(parents=True)
        data = json.loads(TIMED.to_json())
        data["cues"][0]["start"] = -10
        path.write_text(json.dumps(data))
        with (
            patch.object(sources, "captions_from_api", return_value=(TIMED.cues, "en")) as api,
            patch.object(sources, "fetch_title", return_value="Video"),
        ):
            self.assertEqual(sources.fetch_transcript(VIDEO), TIMED)
        api.assert_called_once()

    def test_alternate_captions_are_tried_before_audio(self):
        with (
            patch.object(sources, "captions_from_api", side_effect=CliError("blocked")),
            patch.object(sources, "captions_from_media", return_value=(TIMED.cues, "en", "Video")),
            patch.object(sources, "audio_transcript") as audio,
        ):
            self.assertEqual(sources.fetch_transcript(VIDEO), TIMED)
        audio.assert_not_called()

    def test_missing_captions_fall_back_to_local_transcription(self):
        local = Transcript("Video", VIDEO, TIMED.text, TIMED.cues, "whisper", "en")
        with (
            patch.object(sources, "captions_from_api", side_effect=CliError("no captions")),
            patch.object(sources, "captions_from_media", side_effect=CliError("no subtitles")),
            patch.object(sources, "audio_transcript", return_value=local) as audio,
        ):
            self.assertEqual(sources.fetch_transcript(VIDEO), local)
        audio.assert_called_once()

    def test_no_transcribe_never_downloads_audio(self):
        with (
            patch.object(sources, "captions_from_api", side_effect=CliError("no captions")),
            patch.object(sources, "captions_from_media", side_effect=CliError("no subtitles")),
            patch.object(sources, "audio_transcript") as audio,
            self.assertRaisesRegex(CliError, "disabled"),
        ):
            sources.fetch_transcript(VIDEO, mode="never")
        audio.assert_not_called()

    def test_forced_transcription_bypasses_captions(self):
        with (
            patch.object(sources, "captions_from_api") as api,
            patch.object(sources, "captions_from_media") as alternate,
            patch.object(sources, "audio_transcript", return_value=TIMED) as audio,
        ):
            sources.fetch_transcript(VIDEO, mode="always")
        api.assert_not_called()
        alternate.assert_not_called()
        audio.assert_called_once()

    def test_transcription_settings_invalidate_the_transcript_cache(self):
        paths = {
            sources.transcript_cache_path(VIDEO, ("en",), "auto", "base"),
            sources.transcript_cache_path(VIDEO, ("fr",), "auto", "base"),
            sources.transcript_cache_path(VIDEO, ("en",), "always", "base"),
            sources.transcript_cache_path(VIDEO, ("en",), "auto", "small"),
        }
        self.assertEqual(len(paths), 4)

    def test_missing_whisper_is_reported_before_any_audio_download(self):
        with (
            patch.object(sources, "transcription_available", return_value=False),
            patch.object(sources, "YoutubeDL") as download,
            self.assertRaisesRegex(CliError, "transcribe extra"),
        ):
            sources.audio_transcript(VIDEO, "base", lambda _: None)
        download.assert_not_called()

    def test_downloaded_audio_is_temporary_on_success_and_failure(self):
        for fail in (False, True):
            captured = []

            def downloader(options):
                path = Path(options["outtmpl"].replace("%(ext)s", "webm"))
                path.write_bytes(b"test audio")
                captured.append(path)
                context = Mock()
                context.__enter__ = Mock(return_value=context)
                context.__exit__ = Mock(return_value=False)
                context.extract_info.return_value = {
                    "title": "Video",
                    "requested_downloads": [{"filepath": str(path)}],
                }
                return context

            def transcribe(path, model):
                self.assertTrue(path.is_file())
                if fail:
                    raise CliError("ASR failed")
                return TIMED.cues, "en"

            with (
                self.subTest(fail=fail),
                patch.object(sources, "transcription_available", return_value=True),
                patch.object(sources, "YoutubeDL", side_effect=downloader),
                patch.object(sources, "transcribe_audio", side_effect=transcribe),
            ):
                if fail:
                    with self.assertRaises(CliError):
                        sources.audio_transcript(VIDEO, "base", lambda _: None)
                else:
                    result = sources.audio_transcript(VIDEO, "base", lambda _: None)
                    self.assertEqual(result.source, "whisper")
                    self.assertEqual(result.cues, TIMED.cues)
            self.assertTrue(all(not path.exists() for path in captured))

    def test_whisper_segments_keep_real_start_and_end_values(self):
        model = Mock()
        model.transcribe.return_value = (
            iter([SimpleNamespace(text=" Actual words ", start=12.5, end=16.0)]),
            SimpleNamespace(language="en"),
        )
        with (
            patch.object(sources, "transcription_available", return_value=True),
            patch.object(sources, "whisper_model", return_value=model),
        ):
            self.assertEqual(sources.transcribe_audio(Path("audio.webm")), (TIMED.cues, "en"))

    def test_native_decoder_errors_do_not_escape_as_tracebacks(self):
        with (
            patch.object(sources, "transcription_available", return_value=True),
            patch.object(sources, "whisper_model", side_effect=TypeError("Decoder mismatch")),
            self.assertRaisesRegex(
                CliError, "Local Whisper transcription failed.*Decoder mismatch"
            ),
        ):
            sources.transcribe_audio(Path("audio.webm"))

    def test_playlist_expansion_skips_unavailable_entries_and_uses_the_limit(self):
        context = Mock()
        context.__enter__ = Mock(return_value=context)
        context.__exit__ = Mock(return_value=False)
        context.extract_info.return_value = {
            "entries": [{"id": "20CgG5pUv9Q"}, None, {"id": "kCc8FmEb1nY"}]
        }
        with patch.object(sources, "YoutubeDL", return_value=context) as download:
            urls = sources.list_playlist("https://youtube.com/playlist?list=PLtest", limit=3)
        self.assertEqual(len(urls), 2)
        self.assertEqual(download.call_args.args[0]["playlistend"], 3)
