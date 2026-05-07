"""Data models for read-only tool inspection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ToolManager = Literal["uv", "bun", "pnpm", "mise", "scoop", "brew"]
ToolScope = Literal["active", "candidate", "intentional", "dependency", "unknown"]


@dataclass(frozen=True)
class ToolPathSource:
    """A directory that may contain tool executables."""

    manager: str
    kind: str
    path: Path
    exists: bool
    on_path: bool


@dataclass(frozen=True)
class InstalledTool:
    """A manager-level installed tool or package."""

    name: str
    manager: str
    path: Path
    scope: ToolScope = "unknown"
    version: str | None = None


@dataclass(frozen=True)
class ToolCandidate:
    """An executable candidate found in a manager or PATH directory."""

    name: str
    path: Path
    source: str
    active: bool
    scope: ToolScope = "candidate"
    installed_name: str | None = None


@dataclass(frozen=True)
class ToolInventory:
    """Read-only snapshot of known tool paths and installed manager entries."""

    path_entries: tuple[Path, ...]
    sources: tuple[ToolPathSource, ...]
    installed: tuple[InstalledTool, ...]
    tools: dict[str, tuple[ToolCandidate, ...]]
    duplicates: dict[str, tuple[ToolCandidate, ...]]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManagerResult:
    """Read-only output from one manager backend."""

    sources: tuple[ToolPathSource, ...] = ()
    installed: tuple[InstalledTool, ...] = ()
    issues: tuple[str, ...] = ()
