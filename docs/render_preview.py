"""Regenerate the README's illustrative terminal preview without network access."""

import json
from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.terminal_theme import TerminalTheme
from rich.theme import Theme

from ytsum.models import Transcript
from ytsum.summaries import markdown_summary, validate_summary


def main() -> None:
    console = Console(
        file=StringIO(),
        record=True,
        width=90,
        force_terminal=True,
        color_system="truecolor",
        theme=Theme({"markdown.h1": "bold cyan", "markdown.h2": "bold cyan"}),
    )
    console.print('$ ytsum "https://youtu.be/kCc8FmEb1nY" --brief', style="bold cyan", markup=False)
    console.print()
    fixture = json.loads(Path(__file__).with_name("preview.json").read_text())
    transcript = Transcript.from_data(fixture["transcript"])
    summary = validate_summary(fixture["summary"], {block.id for block in transcript.blocks()})
    console.print(Markdown(markdown_summary(transcript, summary)))
    theme = TerminalTheme(
        background=(15, 23, 42),
        foreground=(226, 232, 240),
        normal=[
            (15, 23, 42),
            (248, 113, 113),
            (134, 239, 172),
            (253, 224, 71),
            (147, 197, 253),
            (216, 180, 254),
            (94, 234, 212),
            (226, 232, 240),
        ],
        bright=[
            (100, 116, 139),
            (252, 165, 165),
            (187, 247, 208),
            (254, 240, 138),
            (191, 219, 254),
            (233, 213, 255),
            (153, 246, 228),
            (248, 250, 252),
        ],
    )
    svg = console.export_svg(
        title="ytsum",
        theme=theme,
        unique_id="ytsum-preview",
    )
    Path(__file__).with_name("terminal.svg").write_text(
        "\n".join(line.rstrip() for line in svg.splitlines()) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
