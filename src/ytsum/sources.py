"""Acquire and cache timed captions, playlists, and local speech recognition."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import tempfile
from functools import lru_cache
from html import unescape
from pathlib import Path

import requests
from youtube_transcript_api import YouTubeTranscriptApi
from yt_dlp import YoutubeDL
from yt_dlp.networking import Request
from yt_dlp.utils import DownloadError

from .inputs import canonical_url, video_id
from .models import CliError, Cue, Transcript, atomic_write, cache_root, clean_text


class TimeoutSession(requests.Session):
    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", 20)
        return super().request(*args, **kwargs)


class QuietLogger:
    def debug(self, message):
        pass

    info = debug
    warning = debug
    error = debug


def media_options(**extra) -> dict:
    runtimes = {name: {} for name in ("deno", "node", "bun") if shutil.which(name)}
    return {
        "quiet": True,
        "no_warnings": True,
        "logger": QuietLogger(),
        "socket_timeout": 20,
        "retries": 2,
        "js_runtimes": runtimes,
        **extra,
    }


def list_playlist(url: str, *, limit: int | None = None) -> list[str]:
    options = media_options(extract_flat="in_playlist", skip_download=True, ignoreerrors=True)
    if limit is not None:
        options["playlistend"] = limit
    try:
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=False)
            entries = list((info or {}).get("entries") or [])
    except (DownloadError, OSError, ValueError) as exc:
        raise CliError(f"Cannot resolve playlist: {clean_text(str(exc))}") from exc
    urls = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id"):
            try:
                urls.append(canonical_url(entry["id"]))
            except CliError:
                continue
    if not urls:
        raise CliError("The playlist has no available public videos.")
    return urls


def fetch_title(url: str) -> str:
    try:
        with TimeoutSession() as session:
            response = session.get(
                "https://www.youtube.com/oembed", params={"url": url, "format": "json"}
            )
            response.raise_for_status()
            title = response.json().get("title")
            if isinstance(title, str) and title.strip():
                return clean_text(title)
    except (requests.RequestException, ValueError, AttributeError):
        pass
    return f"YouTube video {video_id(url)}"


def normalize_caption(text: str) -> str:
    import re

    return " ".join(unescape(re.sub(r"<[^>]+>", "", clean_text(text))).split())


def captions_from_api(url: str, languages: tuple[str, ...]) -> tuple[tuple[Cue, ...], str]:
    with TimeoutSession() as session:
        tracks = list(YouTubeTranscriptApi(http_client=session).list(video_id(url)))
        tracks.sort(
            key=lambda track: (
                track.language_code not in languages,
                track.is_generated,
                languages.index(track.language_code)
                if track.language_code in languages
                else len(languages),
            )
        )
        if not tracks:
            raise CliError("No caption tracks are available.")
        track = tracks[0]
        cues = tuple(
            Cue(text, snippet.start, snippet.start + snippet.duration)
            for snippet in track.fetch()
            if (text := normalize_caption(snippet.text))
        )
    if not cues:
        raise CliError("The caption track contains no speech.")
    return cues, track.language_code


def parse_json3(value: object) -> tuple[Cue, ...]:
    if not isinstance(value, dict) or not isinstance(value.get("events"), list):
        raise CliError("Invalid timed caption response.")
    cues = []
    for event in value["events"]:
        if not isinstance(event, dict) or not event.get("segs"):
            continue
        text = normalize_caption("".join(part.get("utf8", "") for part in event["segs"]))
        if text:
            start = event["tStartMs"] / 1000
            end = start + event.get("dDurationMs", 0) / 1000
            cue = Cue(text, start, end)
            if not cues or cue != cues[-1]:
                cues.append(cue)
    if not cues:
        raise CliError("The caption track contains no speech.")
    return tuple(cues)


def captions_from_media(url: str, languages: tuple[str, ...]) -> tuple[tuple[Cue, ...], str, str]:
    with YoutubeDL(media_options(noplaylist=True, skip_download=True)) as downloader:
        info = downloader.extract_info(url, download=False)
        if not isinstance(info, dict):
            raise CliError("Video metadata is unavailable.")
        tracks = []
        for generated, field in ((False, "subtitles"), (True, "automatic_captions")):
            for language, formats in (info.get(field) or {}).items():
                if language == "live_chat":
                    continue
                for item in formats:
                    if item.get("ext") == "json3" and item.get("url"):
                        tracks.append(
                            (
                                language not in languages,
                                generated,
                                languages.index(language)
                                if language in languages
                                else len(languages),
                                language,
                                item["url"],
                            )
                        )
        if not tracks:
            raise CliError("No timed caption tracks are available.")
        _, _, _, language, caption_url = sorted(tracks)[0]
        with downloader.urlopen(
            Request(caption_url, headers=info.get("http_headers") or {})
        ) as response:
            cues = parse_json3(json.loads(response.read()))
        return cues, language, clean_text(info.get("title") or f"YouTube video {video_id(url)}")


def transcription_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def require_transcription() -> None:
    if not transcription_available():
        raise CliError(
            "Local transcription is not installed. Install the transcribe extra: "
            'uv tool install --reinstall "ytsum[transcribe] @ git+https://github.com/lucasscariot/ytsum.git"'
        )


@lru_cache(maxsize=1)
def whisper_model(name: str):
    from faster_whisper import WhisperModel

    return WhisperModel(name, device="cpu", compute_type="int8")


def transcribe_audio(path: Path, model: str = "base") -> tuple[tuple[Cue, ...], str]:
    require_transcription()
    try:
        segments, info = whisper_model(model).transcribe(str(path), beam_size=1, vad_filter=True)
        cues = tuple(
            Cue(text, segment.start, segment.end)
            for segment in segments
            if (text := normalize_caption(segment.text))
        )
    except Exception as exc:
        # Model downloads and native decoders also raise their own exception
        # types; keep those actionable and let a batch continue with other videos.
        raise CliError(f"Local Whisper transcription failed: {clean_text(str(exc))}") from exc
    if not cues:
        raise CliError("Local transcription found no speech.")
    return cues, info.language


def audio_transcript(url: str, model: str, progress) -> Transcript:
    require_transcription()
    progress("Downloading audio for local transcription…")
    with tempfile.TemporaryDirectory(prefix="ytsum-audio-") as directory:
        options = media_options(
            noplaylist=True, format="bestaudio/best", outtmpl=str(Path(directory) / "audio.%(ext)s")
        )
        try:
            with YoutubeDL(options) as downloader:
                info = downloader.extract_info(url, download=True)
                downloads = (info or {}).get("requested_downloads") or []
                filename = downloads[0].get("filepath") if downloads else None
                path = Path(filename or downloader.prepare_filename(info))
                if path.parent.resolve() != Path(directory).resolve() or not path.is_file():
                    raise CliError("The downloaded audio file is unavailable.")
        except (DownloadError, OSError, TypeError, ValueError) as exc:
            raise CliError(f"Cannot download audio: {clean_text(str(exc))}") from exc
        progress(f"Transcribing locally with Whisper {model} (first use downloads the model)…")
        cues, language = transcribe_audio(path, model)
        title = clean_text(info.get("title") or f"YouTube video {video_id(url)}")
        return Transcript(
            title, url, "\n".join(cue.text for cue in cues), cues, "whisper", language
        )


def transcript_cache_path(url, languages, mode, model) -> Path:
    digest = hashlib.sha256(json.dumps([1, url, languages, mode, model]).encode()).hexdigest()
    return cache_root() / "transcripts" / f"{digest}.json"


def fetch_transcript(
    url: str,
    *,
    languages=("en",),
    mode="auto",
    model="base",
    refresh=False,
    progress=lambda _: None,
) -> Transcript:
    url = canonical_url(url)
    path = transcript_cache_path(url, languages, mode, model)
    if not refresh:
        try:
            cached = Transcript.from_data(json.loads(path.read_text(encoding="utf-8")))
            if cached.url == url:
                return cached
        except (OSError, ValueError, TypeError, KeyError):
            pass
    errors = []
    transcript = None
    if mode != "always":
        progress("Fetching timed captions…")
        try:
            cues, language = captions_from_api(url, languages)
            transcript = Transcript(
                fetch_title(url),
                url,
                "\n".join(cue.text for cue in cues),
                cues,
                "captions",
                language,
            )
        except Exception as exc:
            # Third-party caption errors vary by YouTube client and release. Try
            # the independent extractor, then local ASR; never fabricate timings.
            errors.append(clean_text(str(exc)))
        if transcript is None:
            progress("Trying alternate timed captions…")
            try:
                cues, language, title = captions_from_media(url, languages)
                transcript = Transcript(
                    title, url, "\n".join(cue.text for cue in cues), cues, "captions", language
                )
            except Exception as exc:
                errors.append(clean_text(str(exc)))
    if transcript is None:
        if mode == "never":
            reason = errors[-1][:500] if errors else "No captions available."
            raise CliError(f"Cannot fetch captions; local transcription is disabled. {reason}")
        progress(
            "Captions unavailable; using local transcription…"
            if mode == "auto"
            else "Using local transcription…"
        )
        transcript = audio_transcript(url, model, progress)
    try:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        atomic_write(path, transcript.to_json())
    except OSError:
        progress("Transcript cache unavailable; continuing…")
    return transcript
