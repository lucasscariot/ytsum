"""Normalize copied video links and resolve finite batches without shell evaluation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pyperclip

from .models import CliError, clean_text

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}


def normalize_video_input(value: str) -> str:
    value = clean_text(value).translate(dict.fromkeys(map(ord, "\ufeff\u200b\u200e\u200f")))
    quotes = {'"': '"', "'": "'", "`": "`", "“": "”", "‘": "’", "<": ">"}
    for _ in range(8):
        previous = value
        value = unescape(unquote(value)).strip()
        value = re.sub(r"\\([^\w\s]|_)", r"\1", value)
        if len(value) >= 2 and quotes.get(value[0]) == value[-1]:
            value = value[1:-1].strip()
        markdown = re.fullmatch(r"!?\[[^\n]*\]\(\s*(.*?)\s*\)", value)
        if markdown:
            value = markdown.group(1).strip()
        if value == previous:
            break
    return value


def parsed_link(value: str):
    value = normalize_video_input(value)
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = (parsed.hostname or "").lower()
    except ValueError as exc:
        raise CliError("Invalid video link. Try a youtube.com/watch or youtu.be link.") from exc
    return parsed, host


def canonical_url(value: str) -> str:
    value = normalize_video_input(value)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return f"https://www.youtube.com/watch?v={value}"
    parsed, host = parsed_link(value)
    video_id = None
    if parsed.scheme in {"http", "https"}:
        if host in {"youtu.be", "www.youtu.be"}:
            video_id = parsed.path.strip("/").split("/")[0]
        elif host in YOUTUBE_HOSTS:
            if parsed.path.rstrip("/") == "/watch":
                video_id = parse_qs(parsed.query).get("v", [None])[0]
            else:
                parts = parsed.path.strip("/").split("/")
                if len(parts) == 2 and parts[0] in {"shorts", "embed", "live", "v"}:
                    video_id = parts[1]
    if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise CliError("Provide a YouTube video URL or an 11-character video ID.")
    return f"https://www.youtube.com/watch?v={video_id}"


def video_id(url: str) -> str:
    return parse_qs(urlparse(canonical_url(url)).query)["v"][0]


def playlist_url(value: str, *, force: bool = False) -> str | None:
    parsed, host = parsed_link(value)
    if host not in YOUTUBE_HOSTS or parsed.scheme not in {"http", "https"}:
        if force:
            raise CliError("--playlist requires a YouTube URL containing a list parameter.")
        return None
    playlist = parse_qs(parsed.query).get("list", [""])[0]
    if force or parsed.path.rstrip("/") == "/playlist":
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", playlist):
            raise CliError("The playlist URL is missing a valid list parameter.")
        return f"https://www.youtube.com/playlist?list={playlist}"
    return None


def clipboard_url() -> str:
    try:
        value = pyperclip.paste()
    except (pyperclip.PyperclipException, OSError) as exc:
        raise CliError("Cannot read the clipboard. Pass a YouTube link as an argument.") from exc
    if not value.strip():
        raise CliError("Clipboard is empty. Copy a YouTube link or pass one as an argument.")
    return value


def batch_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


@dataclass(frozen=True)
class Target:
    url: str
    error: str = ""


def resolve_targets(
    values, *, expand, force_playlist=False, limit=None
) -> tuple[list[Target], bool]:
    targets: list[Target] = []
    seen = set()
    batch = len(values) > 1
    for value in values:
        try:
            playlist = playlist_url(value, force=force_playlist)
            batch = batch or playlist is not None
            urls = expand(playlist, limit=limit) if playlist else [canonical_url(value)]
            entries = [Target(canonical_url(url)) for url in urls]
        except CliError as exc:
            entries = [Target(clean_text(value), str(exc))]
        for entry in entries:
            if entry.url not in seen:
                seen.add(entry.url)
                targets.append(entry)
            if limit is not None and len(targets) >= limit:
                return targets, batch
    return targets, batch


def preferred_languages(explicit: str | None = None) -> tuple[str, ...]:
    """Honor explicit preferences, then best-effort migration of ytt's configuration."""
    import json
    import sys

    value = explicit if explicit is not None else os.environ.get("YTSUM_LANGUAGES")
    if value is None:
        paths = [
            Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ytt/config.json"
        ]
        if sys.platform == "darwin":
            paths.insert(0, Path.home() / "Library/Application Support/ytt/config.json")
        for path in paths:
            try:
                languages = json.loads(path.read_text()).get("preferred_languages")
                if (
                    isinstance(languages, list)
                    and languages
                    and all(isinstance(item, str) for item in languages)
                ):
                    value = ",".join(languages)
                    break
            except (OSError, ValueError, AttributeError):
                pass
    languages = tuple(
        dict.fromkeys(item.strip() for item in (value or "en").split(",") if item.strip())
    )
    if not languages or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,35}", item) for item in languages):
        raise CliError("Use comma-separated caption language codes, for example --languages en,fr.")
    return languages
