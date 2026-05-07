"""pnpm path inspection backend."""

from __future__ import annotations

from pathlib import Path

from prescribe.tools.common import InspectionContext, env_path, expand_home, node_modules_installed, source
from prescribe.tools.models import ManagerResult


def inspect(ctx: InspectionContext, path_entries: list[Path]) -> ManagerResult:
    home = env_path(ctx, "PNPM_HOME") or _data_dir(ctx)
    global_bin = _config_path(ctx, "global-bin-dir") or home / "bin"
    global_dir = _config_path(ctx, "global-dir") or home / "global"
    installed = tuple(node_modules_installed(global_dir / "node_modules", manager="pnpm"))
    return ManagerResult(sources=(source("pnpm", "global-bin", global_bin, path_entries),), installed=installed)


def _data_dir(ctx: InspectionContext) -> Path:
    if xdg := env_path(ctx, "XDG_DATA_HOME"):
        return xdg / "pnpm"
    if ctx.is_macos:
        return ctx.home / "Library" / "pnpm"
    if ctx.is_windows:
        return (env_path(ctx, "LOCALAPPDATA") or ctx.home / "AppData" / "Local") / "pnpm"
    return ctx.home / ".local" / "share" / "pnpm"


def _config_path(ctx: InspectionContext, key: str) -> Path | None:
    env_key = f"PNPM_CONFIG_{key.replace('-', '_').upper()}"
    if value := ctx.env.get(env_key):
        return expand_home(value, ctx)
    return None
