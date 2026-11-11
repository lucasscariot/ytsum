"""CLI orchestration for one video, playlists, URL batches, and transcript input."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.theme import Theme

from . import __version__, sources, summaries
from .inputs import batch_lines, clipboard_url, preferred_languages, resolve_targets, video_id
from .models import CliError, Transcript, atomic_write, clean_text
from .summaries import DEFAULT_MODEL, escape_markdown, markdown_summary


def positive_integer(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Use a positive integer.")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Turn YouTube videos and playlists into source-linked notes using local Ollama.",
        epilog='Examples: ytsum "URL" | ytsum URL1 URL2 -d notes | ytsum --batch urls.txt',
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="Video URLs/IDs or a playlist; omit for clipboard, use - for transcript stdin",
    )
    parser.add_argument(
        "--batch",
        action="append",
        default=[],
        metavar="FILE",
        help="Read URLs from a file, one per line; - reads URLs from stdin",
    )
    parser.add_argument(
        "--playlist",
        action="store_true",
        help="Expand the playlist even when a watch URL includes a video ID",
    )
    parser.add_argument(
        "--limit", type=positive_integer, help="Process at most this many unique videos"
    )
    styles = parser.add_mutually_exclusive_group()
    styles.add_argument("--brief", action="store_true", help="Short overview and up to 5 takeaways")
    styles.add_argument("--detailed", action="store_true", help="Include more supporting details")
    parser.add_argument(
        "--model",
        default=os.environ.get("YTSUM_MODEL", DEFAULT_MODEL),
        help="Installed Ollama model (default: %(default)s)",
    )
    destinations = parser.add_mutually_exclusive_group()
    destinations.add_argument(
        "-o", "--output", type=Path, help="Also save a single summary as Markdown"
    )
    destinations.add_argument(
        "-d",
        "--output-dir",
        type=Path,
        help="Directory for video notes and batch index (batch default: ./ytsum-notes)",
    )
    parser.add_argument(
        "--languages",
        help="Preferred caption languages, comma-separated (default: YTSUM_LANGUAGES, ytt preferences, or en)",
    )
    transcription = parser.add_mutually_exclusive_group()
    transcription.add_argument(
        "--transcribe",
        dest="transcription",
        action="store_const",
        const="always",
        help="Download audio and use local Whisper instead of captions",
    )
    transcription.add_argument(
        "--no-transcribe",
        dest="transcription",
        action="store_const",
        const="never",
        help="Require captions; never download audio or run Whisper",
    )
    parser.set_defaults(transcription="auto")
    parser.add_argument(
        "--whisper-model",
        default=os.environ.get("YTSUM_WHISPER_MODEL", "base"),
        help="Local Whisper model (default: %(default)s; first use downloads weights)",
    )
    parser.add_argument(
        "--no-excerpts", action="store_true", help="Hide supporting excerpts; keep timestamp links"
    )
    parser.add_argument(
        "--markdown", action="store_true", help="Print raw Markdown even in a terminal"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="Refresh the transcript and regenerate the summary"
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the summary cache; transcript caching still applies",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="Hide progress and timing")
    parser.add_argument("--version", action="version", version=f"ytsum {__version__}")
    return parser


def generate(transcript: Transcript, args, progress) -> tuple[str, bool]:
    style = "brief" if args.brief else "detailed" if args.detailed else "standard"
    path = summaries.cache_path(transcript, args.model, style)
    summary = None if args.refresh or args.no_cache else summaries.read_cache(path, transcript)
    cached = summary is not None
    if summary is None:
        summary = summaries.summarize(transcript, args.model, style, progress)
        if not args.no_cache:
            try:
                path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                atomic_write(path, json.dumps(summary, ensure_ascii=False))
            except OSError:
                progress("Summary cache unavailable; continuing…")
    return markdown_summary(transcript, summary, excerpts=not args.no_excerpts), cached


def acquire(url: str, args, progress) -> Transcript:
    return sources.fetch_transcript(
        url,
        languages=preferred_languages(args.languages),
        mode=args.transcription,
        model=args.whisper_model,
        refresh=args.refresh,
        progress=progress,
    )


@dataclass(frozen=True)
class BatchEntry:
    title: str
    url: str
    filename: str = ""
    error: str = ""
    cached: bool = False


def batch_markdown(entries: list[BatchEntry]) -> str:
    succeeded = sum(not entry.error for entry in entries)
    lines = [
        "# Video notes",
        "",
        f"{succeeded} saved · {len(entries) - succeeded} failed",
        "",
        "| Video | Result | Notes |",
        "| --- | --- | --- |",
    ]
    for entry in entries:
        title = escape_markdown(entry.title)
        label = title if entry.error else f"[{title}]({entry.url})"
        status = "Failed" if entry.error else "Cached" if entry.cached else "Saved"
        note = f"[Open notes]({entry.filename})" if entry.filename else "—"
        lines.append(f"| {label} | {status} | {note} |")
    failures = [entry for entry in entries if entry.error]
    if failures:
        lines.extend(["", "## Errors", ""])
        lines.extend(
            f"- **{escape_markdown(entry.title)}:** {escape_markdown(entry.error[:1000])}"
            for entry in failures
        )
    return "\n".join(lines).rstrip() + "\n"


def run_batch(targets, args, progress, stderr) -> tuple[str, str, int]:
    if args.output:
        raise CliError("-o saves one video. Use --output-dir for a playlist or batch.")
    directory = (args.output_dir or Path("ytsum-notes")).expanduser()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # Validate the destination before starting downloads or model requests.
        with tempfile.TemporaryFile(dir=directory) as probe:
            probe.write(b"")
    except OSError as exc:
        raise CliError(f"Cannot write batch output to {directory}: {exc}") from exc
    entries = []
    for index, target in enumerate(targets, 1):

        def report(message):
            progress(f"[{index}/{len(targets)}] {message}")

        try:
            if target.error:
                raise CliError(target.error)
            transcript = acquire(target.url, args, report)
            output, cached = generate(transcript, args, report)
            filename = f"{video_id(transcript.url)}.md"
            atomic_write(directory / filename, output)
            entries.append(BatchEntry(transcript.title, transcript.url, filename, cached=cached))
        except (CliError, OSError) as exc:
            error = clean_text(str(exc))
            entries.append(BatchEntry(target.url, target.url, error=error))
            stderr.print(
                f"ytsum: [{index}/{len(targets)}] {target.url}: {error}", style="red", markup=False
            )
        try:
            atomic_write(directory / "index.md", batch_markdown(entries))
        except OSError as exc:
            raise CliError(f"Cannot update the batch index: {exc}") from exc
    failures = sum(bool(entry.error) for entry in entries)
    return (
        batch_markdown(entries),
        f"{len(entries) - failures} notes saved · {failures} failed · {directory}",
        int(failures > 0),
    )


def execute(args, progress, stderr) -> tuple[str, str, int]:
    if "-" in args.urls:
        if args.urls != ["-"] or args.batch or args.playlist:
            raise CliError(
                "Use ytsum - for transcript input, or --batch - for URL input; do not combine them."
            )
        if sys.stdin.isatty():
            raise CliError("Pipe a transcript into ytsum - or provide a YouTube URL.")
        text = clean_text(sys.stdin.read())
        if not text:
            raise CliError("The supplied transcript is empty.")
        if args.output_dir:
            raise CliError("Use -o FILE when saving an untimed transcript summary.")
        transcript = Transcript("Transcript summary", "", text)
        output, cached = generate(transcript, args, progress)
    else:
        values = list(args.urls)
        for filename in args.batch:
            try:
                text = (
                    sys.stdin.read()
                    if filename == "-"
                    else Path(filename).expanduser().read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError) as exc:
                raise CliError(f"Cannot read UTF-8 URL batch {filename}: {exc}") from exc
            values.extend(batch_lines(text))
        if not values:
            if args.batch:
                raise CliError("The batch contains no video links.")
            values = [clipboard_url()]
        progress("Resolving video inputs…")
        targets, playlist_batch = resolve_targets(
            values, expand=sources.list_playlist, force_playlist=args.playlist, limit=args.limit
        )
        if args.batch or len(values) > 1 or playlist_batch:
            return run_batch(targets, args, progress, stderr)
        target = targets[0]
        if target.error:
            raise CliError(target.error)
        transcript = acquire(target.url, args, progress)
        output, cached = generate(transcript, args, progress)
    destination = args.output
    if args.output_dir:
        destination = args.output_dir / f"{video_id(transcript.url)}.md"
    if destination:
        try:
            atomic_write(destination.expanduser(), output)
        except OSError as exc:
            raise CliError(f"Cannot save summary to {destination}: {exc}") from exc
    footer = "cached" if cached else args.model
    if destination:
        footer += f" · saved to {destination}"
    return output, footer, 0


def run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    stderr = Console(stderr=True)
    status = stderr.status("Preparing video notes…", spinner="dots")
    if not args.quiet:
        status.start()
    try:
        output, footer, code = execute(args, status.update, stderr)
    finally:
        status.stop()
    if sys.stdout.isatty() and not args.markdown:
        width = min(100, shutil.get_terminal_size((100, 24)).columns)
        theme = Theme({"markdown.h1": "bold cyan", "markdown.h2": "bold cyan"})
        Console(width=width, theme=theme).print(Markdown(output))
    else:
        sys.stdout.write(output)
    if not args.quiet:
        stderr.print(f"{footer} · {time.monotonic() - started:.1f}s", style="dim", markup=False)
    return code


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run(args)
    except CliError as exc:
        Console(stderr=True).print(f"ytsum: {clean_text(str(exc))}", style="red", markup=False)
        return 1
    except KeyboardInterrupt:
        print("\nytsum: cancelled. Completed batch notes have been preserved.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        with open(os.devnull, "w") as devnull:
            os.dup2(devnull.fileno(), sys.stdout.fileno())
        return 0
    except OSError as exc:
        Console(stderr=True).print(f"ytsum: {clean_text(str(exc))}", style="red", markup=False)
        return 1


if __name__ == "__main__":
    sys.exit(main())
