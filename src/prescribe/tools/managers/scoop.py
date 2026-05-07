"""Scoop path inspection backend."""

from __future__ import annotations

import os
from pathlib import Path

from prescribe.tools.common import InspectionContext, config_path, env_path, iter_dirs, read_json, source
from prescribe.tools.models import InstalledTool, ManagerResult


def inspect(ctx: InspectionContext, path_entries: list[Path]) -> ManagerResult:
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
            )
        )
    return installed
