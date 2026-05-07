"""uv path inspection backend."""

from __future__ import annotations

from pathlib import Path

from prescribe.tools.common import InspectionContext, absolute_env_path, env_path, iter_dirs, read_toml, source
from prescribe.tools.models import InstalledTool, ManagerResult, ToolEntrypoint


def inspect(ctx: InspectionContext, path_entries: list[Path], names: frozenset[str] | None = None) -> ManagerResult:
    state = _state_dir(ctx)
    tool_dir = env_path(ctx, "UV_TOOL_DIR") or state / "tools"
    bin_dir = _executable_dir(ctx, "UV_TOOL_BIN_DIR")
    python_bin_dir = _executable_dir(ctx, "UV_PYTHON_BIN_DIR")
    installed = tuple(_installed_tool(path) for path in iter_dirs(tool_dir))
    return ManagerResult(
        sources=(
            source("uv", "tool-bin", bin_dir, path_entries),
            source("uv", "python-bin", python_bin_dir, path_entries),
        ),
        installed=installed,
    )


def _state_dir(ctx: InspectionContext) -> Path:
    if legacy := _existing_legacy_state(ctx):
        return legacy
    if xdg := absolute_env_path(ctx, "XDG_STATE_HOME"):
        return xdg / "uv"
    if ctx.is_windows:
        base = env_path(ctx, "APPDATA") or ctx.home / "AppData" / "Roaming"
        return base / "uv" / "data"
    if ctx.is_macos:
        return ctx.home / "Library" / "Application Support" / "uv"
    return ctx.home / ".local" / "state" / "uv"


def _existing_legacy_state(ctx: InspectionContext) -> Path | None:
    candidates = [ctx.home / ".uv"]
    if ctx.is_windows:
        candidates.append((env_path(ctx, "APPDATA") or ctx.home / "AppData" / "Roaming") / "uv")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _executable_dir(ctx: InspectionContext, env_var: str) -> Path:
    if override := env_path(ctx, env_var):
        return override
    if xdg_bin := absolute_env_path(ctx, "XDG_BIN_HOME"):
        return xdg_bin
    if xdg_data := absolute_env_path(ctx, "XDG_DATA_HOME"):
        return xdg_data.parent / "bin"
    return ctx.home / ".local" / "bin"


def _installed_tool(path: Path) -> InstalledTool:
    return InstalledTool(
        name=path.name,
        manager="uv",
        path=path,
        scope="intentional",
        entrypoints=_receipt_entrypoints(path / "uv-receipt.toml"),
    )


def _receipt_entrypoints(path: Path) -> tuple[ToolEntrypoint, ...]:
    data = read_toml(path)
    if not isinstance(data, dict):
        return ()
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return ()
    values = tool.get("entrypoints")
    if not isinstance(values, list):
        return ()
    entrypoints: list[ToolEntrypoint] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        name = value.get("name")
        if not name:
            continue
        install_path = value.get("install-path")
        entrypoints.append(
            ToolEntrypoint(
                name=str(name),
                path=Path(str(install_path)) if install_path else None,
                source="uv-receipt",
            )
        )
    return tuple(entrypoints)
