"""Regenerate the README's illustrative terminal preview without network access."""

from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.terminal_theme import TerminalTheme
from rich.theme import Theme

from ytsum.cli import Transcript, markdown_summary


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
    transcript = Transcript(
        title="Let's build GPT: from scratch, in code, spelled out.",
        url="https://www.youtube.com/watch?v=kCc8FmEb1nY",
        text="Illustrative notes based on Andrej Karpathy's GPT-from-scratch tutorial.",
    )
    summary = {
        "summary": (
            "Karpathy builds a small GPT-style language model in PyTorch, starting "
            "with a character-level bigram baseline and developing a decoder-only "
            "Transformer. The walkthrough connects next-token prediction, causal "
            "self-attention and text generation using Shakespeare data."
        ),
        "takeaways": [
            "Start with a bigram model to establish a working training and generation loop.",
            "Use causal self-attention to draw on earlier context without seeing future tokens.",
            "Combine attention heads, feed-forward layers, residual connections and layer normalization.",
            "Compare training and validation loss, then sample new text one token at a time.",
        ],
        "details": [],
        "actions": [],
        "caveats": [],
    }
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
