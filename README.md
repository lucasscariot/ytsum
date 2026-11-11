# ytsum

One command turns a YouTube link into a readable summary, using `dudarev/ytt`
for captions and a local Ollama model for summarization.

```sh
ytsum "https://youtube.com/watch?v=oc8G53Tbsvs"
ytsum "youtube.com/watch?v=oc8G53Tbsvs" --brief
ytsum "URL" --detailed -o notes.md
ytsum                         # Read a YouTube link from the clipboard
ytsum - < transcript.txt       # Summarize an existing transcript
```

The default output includes a short overview, key takeaways, useful supporting
details, and actions or caveats when the source supports them. The source title
and link come from `ytt`. The prompt asks the model to preserve facts and avoid
inventing quotations, timestamps, or advice.

Terminals receive styled headings, wrapped paragraphs, and readable bullets.
Redirection and pipes receive clean Markdown. Use `--markdown` to request raw
Markdown in a terminal. Progress and timing go to stderr. `-o FILE` also saves
the Markdown, creating parent directories and replacing an existing destination.
The clipboard is read only when no URL is supplied, and is never overwritten.

## Copied links

Links can include literal shell escapes, percent-encoded punctuation, HTML entities,
or surrounding quotes, backticks, angle brackets, or Markdown link syntax. These
forms are normalized before fetching, including when read from the clipboard:

```sh
ytsum 'https://www.youtube.com/watch\?v\=20CgG5pUv9Q'
ytsum '[Watch](https://youtu.be/20CgG5pUv9Q)'
ytsum 20CgG5pUv9Q
```

Ordinary quoted URLs also work. Tracking parameters and timestamps are discarded
after identifying the video. Unrelated sites and malformed video IDs are rejected
with a readable error; URL text is never executed as shell code.

## Setup

Requires Python 3.12+, `uv`, `ytt` from https://github.com/dudarev/ytt, and Ollama.

```sh
brew services start ollama
ollama pull qwen3.5:9b-q4_K_M
uv tool install /absolute/path/to/this/checkout
```

The installed default is `qwen3.5:9b-q4_K_M`, with thinking disabled, a low temperature,
and bounded output. This explicit 4-bit variant supports schema-constrained output
instead of selecting the MLX backend automatically. Models stay warm for ten minutes.
The 9B model was chosen after comparing it with 4B on a real transcript: it
preserved important qualifications better, while taking about 29 seconds on the
M4 Max for the test video. This is one example, not a general benchmark.
For faster, less precise summaries, use the also-installed
`--model qwen3.5:4b-q4_K_M`. Set `YTSUM_MODEL` to change the default. The selected model must
follow JSON instructions and support disabling thinking. `OLLAMA_HOST` selects the
server; the default is `http://127.0.0.1:11434`. With that default, inference stays
on this Mac. Only fetching captions contacts YouTube.

## Efficiency and long videos

Completed summaries are cached in `${XDG_CACHE_HOME:-~/.cache}/ytsum`, keyed by
the full transcript, model name, summary style, and prompt version. This avoids
running inference again for the same input. Caption caching is provided by `ytt`.
`--refresh` refreshes both captions and the summary. `--no-cache` bypasses only
the summary cache. Refresh after updating weights behind an existing model tag.

Inputs are processed in full. Long transcripts are split at paragraph/word
boundaries, summarized in order, and combined; very long note sets are reduced
in stages. Each request uses an explicit context allocation up to 65,536 tokens
with a conservative UTF-8 byte budget and reserved space for the prompt/output.
Long videos therefore take additional calls, and compression can omit details.
Captions can also contain transcription errors; check the original for exact
quotes or consequential claims. Empty captions, interrupted generation, invalid
model responses, and truncated output fail with an error instead of producing a
partial document. Errors do not overwrite the requested output file.

## Commands

```sh
ytsum --help
ytsum "URL" --brief                         # Roughly 120-180 words
ytsum "URL" --detailed                      # Roughly 500-700 words
ytsum "URL" --model qwen3.5:4b-q4_K_M        # Faster, less precise summaries
ytsum "URL" --refresh                       # Fetch and generate again
ytsum "URL" | tee summary.md                # Display and save raw Markdown
ytsum "URL" -q --markdown                   # Hide progress and timing
```

`ytt`'s preferred caption languages still apply. The summary is in English.
Summary lengths are targets, not fixed requirements; short videos get shorter
summaries. Unsupported or empty sections are omitted. Summaries use validated
JSON and deterministic Markdown rendering for consistent formatting. Ollama's
schema-constrained output is used when available. The MLX backend falls back to
schema instructions in the prompt, with the same validation before rendering.

## Development

```sh
uv sync --python 3.12
uv run python -m unittest discover -s tests -v
uvx ruff check .
uvx ruff format --check .
uv tool install --reinstall /absolute/path/to/this/checkout
```

This is a local repository without a configured GitHub publication target.
Uninstall with `uv tool uninstall ytsum`. This preserves the source, downloaded
Ollama model, and caches.
