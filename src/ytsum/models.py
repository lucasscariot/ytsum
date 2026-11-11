"""Source data shared by acquisition, summarization, and presentation."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


class CliError(Exception):
    """An actionable error suitable for terminal output."""


def clean_text(text: str) -> str:
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text).strip()


def cache_root() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ytsum"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass(frozen=True)
class Cue:
    text: str
    start: float | None = None
    end: float | None = None

    def __post_init__(self):
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("A transcript segment must contain text.")
        for value in (self.start, self.end):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("Transcript timestamps must be finite, nonnegative numbers.")
        if self.end is not None and (self.start is None or self.end < self.start):
            raise ValueError("Transcript segment end precedes its start.")


@dataclass(frozen=True)
class SourceBlock:
    id: str
    text: str
    start: float | None
    end: float | None


@dataclass(frozen=True)
class Transcript:
    title: str
    url: str
    text: str
    cues: tuple[Cue, ...] = ()
    source: str = "text"
    language: str = ""

    def blocks(self) -> tuple[SourceBlock, ...]:
        """Give short source passages stable IDs; retain only actual source timings."""
        cues = self.cues or (Cue(self.text),)
        blocks = []
        words: list[str] = []
        start = end = None

        def flush():
            nonlocal words, start, end
            if words:
                blocks.append(SourceBlock(f"S{len(blocks) + 1}", " ".join(words), start, end))
                words, start, end = [], None, None

        for cue in cues:
            if words and (
                len(" ".join(words)) + len(cue.text) > 400
                or (start is not None and cue.start is not None and cue.start - start >= 20)
            ):
                flush()
            for word in cue.text.split():
                if words and len(" ".join(words)) + len(word) > 500:
                    flush()
                if not words:
                    start = cue.start
                words.append(word)
                end = cue.end
        flush()
        return tuple(blocks)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_data(cls, value: object) -> Transcript:
        if not isinstance(value, dict):
            raise ValueError("Invalid transcript cache.")
        for field in ("title", "url", "text", "source", "language"):
            if not isinstance(value.get(field), str):
                raise ValueError("Invalid transcript cache.")
        if not value["text"].strip() or not isinstance(value.get("cues"), list):
            raise ValueError("Invalid transcript cache.")
        cues = tuple(Cue(**cue) for cue in value["cues"])
        if not cues or value["source"] not in {"captions", "whisper", "text"}:
            raise ValueError("Invalid transcript cache.")
        return cls(**{**value, "cues": cues})
