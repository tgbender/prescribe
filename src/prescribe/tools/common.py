"""Shared helpers for read-only tool inspection backends."""

from __future__ import annotations

import json
import os
import stat
import sys
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from prescribe.tools.models import InstalledTool, ToolPathSource


@dataclass(frozen=True)
class InspectionContext:
    env: dict[str, str]
    home: Path
    platform: str

    @property
    def is_windows(self) -> bool:
        return self.platform == "win32"

    @property
    def is_macos(self) -> bool:
        return self.platform == "darwin"

    @property
    def is_linux(self) -> bool:
        return self.platform.startswith("linux")


def context(*, env: dict[str, str] | None, home: Path | str | None, platform: str | None) -> InspectionContext:
    merged = dict(os.environ if env is None else env)
    home_path = Path(home or merged.get("USERPROFILE") or merged.get("HOME") or Path.home())
    return InspectionContext(env=merged, home=home_path, platform=platform or sys.platform)


def path_entries(ctx: InspectionContext) -> list[Path]:
    raw = ctx.env.get("PATH", "")
    return [normalize_path(entry) for entry in raw.split(os.pathsep) if entry]


def path_sources(entries: list[Path]) -> list[ToolPathSource]:
    return [
        ToolPathSource(manager="path", kind="path", path=entry, exists=entry.is_dir(), on_path=True)
        for entry in entries
    ]


def source(manager: str, kind: str, path: Path, entries: list[Path]) -> ToolPathSource:
    normalized = normalize_path(path)
    return ToolPathSource(
        manager=manager,
        kind=kind,
        path=normalized,
        exists=normalized.is_dir(),
        on_path=path_on_path(normalized, entries),
    )


def iter_dirs(path: Path) -> Iterator[Path]:
    try:
        children = list(path.iterdir())
    except OSError:
        return
    for child in sorted(children, key=lambda item: item.name.casefold()):
        try:
            if child.is_dir():
                yield normalize_path(child)
        except OSError:
            continue


def iter_executables(directory: Path, names: frozenset[str] | None, ctx: InspectionContext) -> Iterator[Path]:
    if names is not None:
        for name in sorted(names):
            for candidate in candidate_paths(directory, name, ctx):
                if candidate.is_file():
                    yield normalize_path(candidate)
        return
    try:
        children = list(directory.iterdir())
    except OSError:
        return
    for child in children:
        try:
            if child.is_file() and is_executable(child, ctx):
                yield normalize_path(child)
        except OSError:
            continue


def candidate_paths(directory: Path, name: str, ctx: InspectionContext) -> tuple[Path, ...]:
    if Path(name).suffix:
        return (directory / name,)
    if not ctx.is_windows:
        return (directory / name,)
    return tuple(directory / f"{name}{suffix}" for suffix in pathext(ctx))


def is_executable(path: Path, ctx: InspectionContext) -> bool:
    if ctx.is_windows:
        return path.suffix.lower() in {item.lower() for item in pathext(ctx)}
    mode = path.stat().st_mode
    return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def pathext(ctx: InspectionContext) -> tuple[str, ...]:
    raw = ctx.env.get("PATHEXT", ".COM;.EXE;.BAT;.CMD;.PS1")
    return tuple(ext.lower() for ext in raw.split(";") if ext)


def tool_name(path: Path, ctx: InspectionContext) -> str:
    if ctx.is_windows and path.suffix.lower() in {ext.lower() for ext in pathext(ctx)}:
        return path.stem
    return path.name


def path_on_path(path: Path, entries: list[Path]) -> bool:
    key = case_key(path)
    return any(case_key(entry) == key for entry in entries)


def case_key(path: Path) -> str:
    return str(normalize_path(path)).casefold()


def normalize_path(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def env_path(ctx: InspectionContext, name: str) -> Path | None:
    value = ctx.env.get(name)
    return expand_home(value, ctx) if value else None


def absolute_env_path(ctx: InspectionContext, name: str) -> Path | None:
    path = env_path(ctx, name)
    return path if path is not None and path.is_absolute() else None


def expand_home(value: str, ctx: InspectionContext) -> Path:
    if value == "~" or value.startswith("~/") or value.startswith("~\\"):
        return normalize_path(ctx.home / value[2:])
    return normalize_path(Path(value))


def xdg_data(ctx: InspectionContext) -> Path:
    if xdg := env_path(ctx, "XDG_DATA_HOME"):
        return xdg
    if ctx.is_windows:
        return env_path(ctx, "LOCALAPPDATA") or ctx.home / "AppData" / "Local"
    return ctx.home / ".local" / "share"


def config_path(config: object, key: str, ctx: InspectionContext) -> Path | None:
    if not isinstance(config, dict):
        return None
    value = config.get(key)
    return expand_home(str(value), ctx) if value else None


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_toml(path: Path) -> object:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None


def node_modules_installed(node_modules: Path, *, manager: str) -> list[InstalledTool]:
    installed: list[InstalledTool] = []
    for child in iter_dirs(node_modules):
        if child.name.startswith("."):
            continue
        if child.name.startswith("@"):
            for scoped in iter_dirs(child):
                installed.append(
                    InstalledTool(
                        name=f"{child.name}/{scoped.name}",
                        manager=manager,
                        path=scoped,
                        scope="intentional",
                    )
                )
            continue
        installed.append(InstalledTool(name=child.name, manager=manager, path=child, scope="intentional"))
    return installed
