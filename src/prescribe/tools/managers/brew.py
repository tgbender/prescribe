"""Homebrew path inspection backend."""

from __future__ import annotations

from pathlib import Path

from prescribe.tools.common import InspectionContext, iter_dirs, normalize_path, read_json, source
from prescribe.tools.models import InstalledTool, ManagerResult, ToolScope


def inspect(ctx: InspectionContext, path_entries: list[Path]) -> ManagerResult:
    prefix = _prefix(ctx)
    cellar = prefix / "Cellar"
    return ManagerResult(
        sources=(
            source("brew", "bin", prefix / "bin", path_entries),
            source("brew", "sbin", prefix / "sbin", path_entries),
        ),
        installed=tuple(_installed(cellar)),
    )


def _prefix(ctx: InspectionContext) -> Path:
    if value := ctx.env.get("HOMEBREW_PREFIX"):
        return normalize_path(Path(value))
    if ctx.is_macos and Path("/opt/homebrew/bin/brew").exists():
        return Path("/opt/homebrew")
    if ctx.is_linux:
        return Path("/home/linuxbrew/.linuxbrew")
    return Path("/usr/local")


def _installed(cellar: Path) -> list[InstalledTool]:
    installed: list[InstalledTool] = []
    for formula in iter_dirs(cellar):
        versions = list(iter_dirs(formula))
        if not versions:
            continue
        version_dir = versions[-1]
        scope: ToolScope = "unknown"
        data = read_json(version_dir / "INSTALL_RECEIPT.json")
        if isinstance(data, dict):
            scope = "intentional" if data.get("installed_on_request") is True else "dependency"
        installed.append(
            InstalledTool(name=formula.name, manager="brew", path=formula, scope=scope, version=version_dir.name)
        )
    return installed
