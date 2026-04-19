from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class Document:
    path: Path
    format: str
    root: Any
    source_text: str | None = None
    baseline_root: Any | None = None
    syntax: Any | None = None


class Adapter(Protocol):
    format_name: str

    def load(self, path: Path) -> Document: ...

    def dump(self, document: Document, path: Path) -> None: ...
