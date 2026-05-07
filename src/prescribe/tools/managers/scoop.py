"""Scoop path inspection backend."""

from __future__ import annotations

import os
from pathlib import Path

from prescribe.tools.common import InspectionContext, config_path, env_path, iter_dirs, read_json, source
from prescribe.tools.models import InstalledTool, ManagerResult, ToolEntrypoint


def inspect(ctx: InspectionContext, path_entries: list[Path], names: frozenset[str] | None = None) -> ManagerResult:
    if not ctx.is_windows:
        return ManagerResult()
    config_home = env_path(ctx, "XDG_CONFIG_HOME") or ctx.home / ".config"
    config = read_json(config_home / "scoop" / "config.json")
    root = env_path(ctx, "SCOOP") or config_path(config, "ROOT_PATH", ctx) or ctx.home / "scoop"
    program_data = env_path(ctx, "COMMONAPPLICATIONDATA") or Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    global_root = env_path(ctx, "SCOOP_GLOBAL") or config_path(config, "GLOBAL_PATH", ctx) or program_data / "scoop"
    cache = env_path(ctx, "SCOOP_CACHE") or config_path(config, "CACHE_PATH", ctx) or root / "cache"
    installed = _installed(root, "scoop") + _installed(global_root, "scoop-global")
    return ManagerResult(
        sources=(
            source("scoop", "shims", root / "shims", path_entries),
            source("scoop", "global-shims", global_root / "shims", path_entries),
            source("scoop", "cache", cache, path_entries),
        ),
        installed=tuple(installed),
    )


def _installed(root: Path, manager: str) -> list[InstalledTool]:
    entrypoints = _shim_entrypoints(root)
    installed: list[InstalledTool] = []
    for app in iter_dirs(root / "apps"):
        if app.name.lower() == "scoop":
            continue
        current = app / "current"
        installed.append(
            InstalledTool(
                name=app.name,
                manager=manager,
                path=current if current.exists() else app,
                scope="intentional",
                entrypoints=tuple(entrypoints.get(app.name.casefold(), ())),
            )
        )
    return installed


def _shim_entrypoints(root: Path) -> dict[str, list[ToolEntrypoint]]:
    by_app: dict[str, list[ToolEntrypoint]] = {}
    shims = root / "shims"
    try:
        children = list(shims.iterdir())
    except OSError:
        return by_app
    for child in children:
        if child.suffix.casefold() != ".shim":
            continue
        target = _shim_target(child)
        if target is None:
            continue
        parts = [part.casefold() for part in target.parts]
        try:
            app_index = parts.index("apps") + 1
        except ValueError:
            continue
        if app_index >= len(target.parts):
            continue
        app = target.parts[app_index]
        by_app.setdefault(app.casefold(), []).append(
            ToolEntrypoint(name=child.stem, path=_shim_executable(root, child.stem), source="scoop-shim")
        )
    return by_app


def _shim_target(path: Path) -> Path | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip().casefold() == "path":
            return Path(value.strip().strip('"'))
    return None


def _shim_executable(root: Path, name: str) -> Path:
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        candidate = root / "shims" / f"{name}{suffix}"
        if candidate.exists():
            return candidate
    return root / "shims" / f"{name}.shim"
