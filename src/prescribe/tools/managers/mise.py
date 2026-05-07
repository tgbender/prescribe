"""mise path inspection backend."""

from __future__ import annotations

from pathlib import Path

from prescribe.tools.common import InspectionContext, env_path, iter_dirs, read_toml, source, xdg_data
from prescribe.tools.models import InstalledTool, ManagerResult


def inspect(ctx: InspectionContext, path_entries: list[Path]) -> ManagerResult:
    data = env_path(ctx, "MISE_DATA_DIR") or xdg_data(ctx) / "mise"
    installs = env_path(ctx, "MISE_INSTALLS_DIR") or data / "installs"
    shims = env_path(ctx, "MISE_SHIMS_DIR") or data / "shims"
    return ManagerResult(sources=(source("mise", "shims", shims, path_entries),), installed=tuple(_installed(installs)))


def _installed(installs: Path) -> list[InstalledTool]:
    manifest = read_toml(installs / ".mise-installs.toml")
    installed: list[InstalledTool] = []
    if isinstance(manifest, dict):
        for folder, info in manifest.items():
            if not isinstance(info, dict):
                continue
            name = _short_name(str(info.get("short") or info.get("full") or folder))
            installed.append(InstalledTool(name=name, manager="mise", path=installs / folder, scope="intentional"))
    known = {str(tool.path).casefold() for tool in installed}
    for path in iter_dirs(installs):
        if path.name.startswith(".") or path.name == "incomplete" or str(path).casefold() in known:
            continue
        installed.append(InstalledTool(name=path.name, manager="mise", path=path, scope="unknown"))
    return installed


def _short_name(value: str) -> str:
    return value.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
