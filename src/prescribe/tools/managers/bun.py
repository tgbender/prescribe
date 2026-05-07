"""Bun path inspection backend."""

from __future__ import annotations

from pathlib import Path

from prescribe.tools.common import InspectionContext, env_path, node_modules_installed, source
from prescribe.tools.models import ManagerResult


def inspect(ctx: InspectionContext, path_entries: list[Path], names: frozenset[str] | None = None) -> ManagerResult:
    install = env_path(ctx, "BUN_INSTALL") or ctx.home / ".bun"
    cache_base = env_path(ctx, "XDG_CACHE_HOME") or ctx.home
    global_dir = env_path(ctx, "BUN_INSTALL_GLOBAL_DIR") or install / "install" / "global"
    if "BUN_INSTALL" not in ctx.env:
        global_dir = cache_base / ".bun" / "install" / "global"
    bin_dir = env_path(ctx, "BUN_INSTALL_BIN") or install / "bin"
    if "BUN_INSTALL" not in ctx.env and "BUN_INSTALL_BIN" not in ctx.env:
        bin_dir = cache_base / ".bun" / "bin"
    installed = tuple(node_modules_installed(global_dir / "node_modules", manager="bun"))
    return ManagerResult(sources=(source("bun", "global-bin", bin_dir, path_entries),), installed=installed)
