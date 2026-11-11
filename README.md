<div align="center">

# ytsum

**A YouTube link in. Useful, source-linked notes out.**

Summarize videos and playlists with local AI. Jump back to the source,
read supporting excerpts, and keep an organized folder of Markdown notes.

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

</div>

```sh
ytsum "https://youtu.be/kCc8FmEb1nY"
```

![Illustrative summary of Andrej Karpathy's GPT-from-scratch tutorial](docs/terminal.svg)

*Illustrative notes with real caption passages and timestamps from [Andrej Karpathy's GPT-from-scratch tutorial](https://youtu.be/kCc8FmEb1nY). Actual model output varies.*

## What you get

- **Checkable takeaways:** clickable timestamps and supporting excerpts taken from the transcript.
- **Playlists and batches:** one Markdown note per video, plus a linked index. Individual failures do not stop the rest.
- **Videos without captions:** automatic local Whisper transcription when caption retrieval fails.
- **Local inference:** Ollama summarizes on your computer. No cloud account or API key required.
- **Readable output:** styled terminal notes; clean Markdown when saved or piped.
- **Efficient reruns:** timed transcripts and summaries are cached separately. Long transcripts are processed in parts without discarding source text.
- **Flexible input:** full links, short links, Shorts, video IDs, clipboard links, URL files, and transcript stdin.

The tool summarizes speech. It does not analyze on-screen visuals.

## Quickstart

### 1. Install the prerequisites

You need [uv](https://docs.astral.sh/uv/getting-started/installation/),
[Git](https://git-scm.com/downloads), and [Ollama](https://ollama.com/download).
uv manages Python 3.12 for the commands below. A supported JavaScript runtime
helps yt-dlp download YouTube audio; [Node.js](https://nodejs.org/) is one option.

On macOS with Homebrew:

```sh
brew install uv ollama node
brew services start ollama
```

On Linux or Windows, follow the tools' official installation instructions.
Start the Ollama app or run `ollama serve` before continuing. End-to-end usage
has been tested on Apple Silicon macOS; Linux and Windows remain unverified.

### 2. Install ytsum

```sh
uv tool install --python 3.12 "ytsum[transcribe] @ git+https://github.com/lucasscariot/ytsum.git"
uv tool update-shell
```

Open a new terminal if uv updated your shell configuration. The `transcribe`
extra includes local Whisper and its audio decoder. For a smaller, captions-only
installation, omit `[transcribe]` and use `--no-transcribe`.

**Upgrading from 0.1?** The separate `ytt` command is no longer required.
Existing `ytt` language preferences are reused when available.

### 3. Download a summary model

```sh
ollama pull qwen3.5:9b-q4_K_M
```

The default model download is about **6.6 GB**. Running it also needs memory
for transcript context. See [choosing a model](#choosing-a-model) for a smaller option.

### 4. Summarize a video

```sh
ytsum "https://youtu.be/kCc8FmEb1nY"
```

The first run retrieves captions and generates notes. Repeating the same request
uses the local caches. If captions cannot be retrieved, ytsum downloads audio
and transcribes it locally; the first such run also downloads Whisper weights.

## Everyday use

| Want to… | Run |
| --- | --- |
| Get the usual overview and notes | `ytsum "URL"` |
| Read a shorter summary | `ytsum "URL" --brief` |
| Keep more supporting detail | `ytsum "URL" --detailed` |
| Read the clipboard | `ytsum` |
| Save Markdown and display the result | `ytsum "URL" -o notes.md` |
| Hide excerpts while keeping time links | `ytsum "URL" --no-excerpts` |
| Prefer English, then French captions | `ytsum "URL" --languages en,fr` |
| Summarize a transcript file | `ytsum - < transcript.txt` |
| Refresh the transcript and summary | `ytsum "URL" --refresh` |

Replace `URL` with a YouTube link. Quote links so your shell handles `?` and `&`.
Copied shell escapes, Markdown links, percent encoding, HTML entities, and
surrounding quotes or angle brackets are normalized automatically:

```sh
ytsum kCc8FmEb1nY
ytsum 'https://www.youtube.com/watch\?v\=kCc8FmEb1nY'
ytsum '[Watch this](https://youtu.be/kCc8FmEb1nY)'
```

The normal summary targets 200–300 words, excluding excerpts, with an overview,
key takeaways, useful details, and actions/caveats when supported. `--brief`
targets 120–180 words; `--detailed` targets 500–700. Short sources get shorter
summaries. Summaries are in English.

### Timestamps and excerpts

Each takeaway cites one or two source passages. The application builds time links
from the passages' actual caption or Whisper timings and shows a short excerpt
from the first passage. The model selects passage IDs; it does not supply the
quoted text or timestamp. Unknown IDs are rejected before notes are saved.

Excerpts may be shortened with an ellipsis. They reproduce caption/transcription
text with normalized whitespace, which can contain recognition errors. Links
point to the beginning of a supporting passage, rather than an exact word.
Whisper timestamps are labeled approximate. Plain transcript input receives
excerpts without invented time links.

Valid citations do not prove that every claim is supported. Check the linked
passage when exact numbers, qualifications, or wording matter.

### Playlists and batches

```sh
# Multiple videos, in the order supplied
ytsum "https://youtu.be/kCc8FmEb1nY" "https://youtu.be/VMj-3S1tku0" -d notes

# Karpathy's Zero to Hero playlist, capped at three videos
ytsum "https://www.youtube.com/playlist?list=PLAqhIrjkxbuWI23v9cThsA9GvCAUhRvKZ" --limit 3 -d course-notes

# A watch link containing both v= and list= needs explicit playlist expansion
ytsum "https://www.youtube.com/watch?v=VIDEO_ID&list=PLAYLIST_ID" --playlist -d notes

# One URL or video ID per line; blank lines and # comments are ignored
ytsum --batch urls.txt -d notes
cat urls.txt | ytsum --batch - -d notes
```

Replace `PLAYLIST_ID` and `VIDEO_ID` with real IDs. A plain playlist URL expands
automatically. A video link containing a playlist parameter summarizes just that
video unless `--playlist` is supplied. Duplicate videos are processed once.
`--limit` caps resolved entries across the run; unavailable playlist entries may
be skipped by YouTube's extractor.

```text
notes/
├── index.md           # Linked results and any per-video errors
├── kCc8FmEb1nY.md      # Stable video ID filenames
└── VMj-3S1tku0.md
```

Batch output defaults to `./ytsum-notes`. The index is updated after each video,
so completed notes survive cancellation. A failed video preserves any existing
note for that video; its old note is not counted as a success in the new index.
Rerun the batch to reuse cached results and retry failures. Batches return exit
code **1** if any item fails, **0** when all succeed; cancellation returns **130**.

`-o FILE` saves one summary. Use `-d DIRECTORY` for batches. Successful runs
replace existing note files and the index; each file is written atomically.
Different styles/models for the same video share a note filename, so use separate
output folders if you want to keep multiple versions.

### Local transcription

Caption retrieval tries two extractors before falling back to downloaded audio.
You can also control that choice:

```sh
# Always transcribe the audio locally
ytsum "URL" --transcribe

# Require captions; never download audio or run Whisper
ytsum "URL" --no-transcribe

# Use a larger Whisper model for transcription
ytsum "URL" --transcribe --whisper-model small
```

The default Whisper `base` model uses CPU inference with int8 weights through
[faster-whisper](https://github.com/SYSTRAN/faster-whisper). Models download once
from Hugging Face, then stay cached. Larger models need more disk, memory, and
time. Downloaded audio is temporary and removed after success, failure, or normal
cancellation. PyAV bundles the audio decoder; a separate FFmpeg executable is
not required by this transcription path.

Whisper runs locally; no audio is sent to a cloud transcription service. YouTube
must still allow audio retrieval. Private, restricted, or blocked videos may fail,
and the tool does not import browser cookies automatically.

### Files and pipes

```sh
ytsum "https://youtu.be/kCc8FmEb1nY" --detailed -o notes/video.md
ytsum "https://youtu.be/kCc8FmEb1nY" > summary.md
cat transcript.txt | ytsum - --brief
```

Progress goes to stderr; stdout contains Markdown when piped. Use `--markdown`
to print raw Markdown in a terminal too. `-o` creates parent directories and
preserves an existing file when generation fails. Shell redirection (`>`) is
controlled by your shell and can truncate a file before ytsum starts. The
clipboard is read only.

## Choosing a model

| Model | Download | Suggested use |
| --- | --- | --- |
| `qwen3.5:9b-q4_K_M` | ~6.6 GB | Default; a balance of speed and fidelity |
| `qwen3.5:4b-q4_K_M` | ~3.3 GB | Faster, with a greater chance of missing or misstating details |

These are [Ollama model tags](https://ollama.com/library/qwen3.5/tags).
The explicit `q4_K_M` variants support constrained JSON output. Thinking is disabled
to reduce latency; models stay loaded for up to ten minutes between requests.

```sh
ollama pull qwen3.5:4b-q4_K_M
ytsum "URL" --model qwen3.5:4b-q4_K_M
export YTSUM_MODEL=qwen3.5:4b-q4_K_M
```

Other models must follow the sourced JSON structure. Backends that report
constrained output unavailable fall back to JSON instructions; citation and
structure validation still apply.

## Configuration and caching

Run `ytsum --help` for all options.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `YTSUM_MODEL` | `qwen3.5:9b-q4_K_M` | Summary model |
| `YTSUM_LANGUAGES` | `en`, or existing ytt preferences | Comma-separated preferred caption languages |
| `YTSUM_WHISPER_MODEL` | `base` | Local transcription model |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama server address |
| `XDG_CACHE_HOME` | `~/.cache` | Parent of `ytsum/transcripts/` and `ytsum/summaries/` |

Explicit command flags override environment defaults. Caption languages are
preferences: a manually supplied track in a requested language is preferred to
auto-generated captions; other available languages are tried if necessary.
They do not select Whisper's language, which is detected from the audio.

Timed transcript caches include the URL, caption languages, transcription mode,
and Whisper model. Summary caches include the transcript and timing data, summary
model, style, and prompt version. `--no-excerpts` only changes rendering and can
reuse the same summary. `--no-cache` bypasses summary caching while retaining
transcript caching; `--refresh` reacquires the transcript and regenerates notes.
Use `--refresh` after updating weights behind an existing model tag.

Caches contain transcript text and notes. Whisper and Ollama manage their own
model-weight caches. Fetching captions/audio and first-time model downloads need
internet access. With the default local server, inference runs on your computer;
a remote `OLLAMA_HOST` sends the transcript to that server instead.

Long transcripts are summarized in ordered parts and combined while preserving
source IDs. Requests have explicit context allocations up to 65,536 tokens.
Longer videos require more model calls; compression can omit details.

## Troubleshooting

| Problem | What to try |
| --- | --- |
| `ytsum` is not found | Run `uv tool update-shell`, then open a new terminal |
| Local transcription is not installed | Reinstall using the `[transcribe]` quickstart command, or use `--no-transcribe` |
| Audio cannot be downloaded | Install a supported JS runtime such as Node.js; update ytsum and its dependencies; check that the video is public and playable |
| Model download fails | Check internet access and free disk space; retry the same command |
| Ollama cannot be reached | Open Ollama, run `ollama serve`, or use `brew services start ollama` on macOS |
| Summary model is unavailable | Run `ollama pull qwen3.5:9b-q4_K_M`, or pull your selected model |
| Clipboard cannot be read | Pass a link directly; Linux may need a clipboard backend |
| Notes look stale | Use `--refresh` |
| Generation is slow | Try the 4B model and `--brief`; audio transcription and long transcripts take longer |
| Model output or citation is invalid | Try `--brief`, `--refresh`, or another model; no partial summary is saved |
| One batch video failed | Read `index.md` for the error, then rerun; completed summaries are cached |

## Update or uninstall

```sh
uv tool install --reinstall --upgrade --python 3.12 "ytsum[transcribe] @ git+https://github.com/lucasscariot/ytsum.git"
uv tool uninstall ytsum
```

Uninstalling keeps models and local caches.

## Development

```sh
git clone https://github.com/lucasscariot/ytsum.git
cd ytsum
uv sync --locked --extra transcribe
uv run --extra transcribe ruff check .
uv run --extra transcribe ruff format --check .
uv run --extra transcribe python -m unittest discover -s tests -v
uv build --no-sources
```

Automated tests mock network/model responses and cover source timing, invalid
citations, long-input coverage, fallback and cleanup, batch failures, output
preservation, and caching. They need no running Ollama or model downloads.
Smoke-test the wheel with `uvx --from ./dist/ytsum-0.2.0-py3-none-any.whl ytsum --help`.
To update an installed command from a local checkout, run
`uv tool install --reinstall '.[transcribe]'`.

Bug reports and improvements are welcome through [issues](https://github.com/lucasscariot/ytsum/issues)
and pull requests. Include your OS, `ytsum --version`, the command, and the error
message so the problem can be reproduced.

## Credits and license

Built with [youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api),
[yt-dlp](https://github.com/yt-dlp/yt-dlp),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper),
[Ollama](https://ollama.com/), [Rich](https://github.com/Textualize/rich),
and [pyperclip](https://github.com/asweigart/pyperclip).
The original caption workflow used [dudarev/ytt](https://github.com/dudarev/ytt).

[MIT License](LICENSE) · Copyright © 2026 Lucas
