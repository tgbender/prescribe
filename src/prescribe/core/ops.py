from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Operation:
    kind: str
    path: Path
    value: Any = None
