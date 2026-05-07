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
    installed = _installed(root, "scoop", names) + _installed(global_root, "scoop-global", names)
    return ManagerResult(
        sources=(
            source("scoop", "shims", root / "shims", path_entries),
            source("scoop", "global-shims", global_root / "shims", path_entries),
        ),
        installed=tuple(installed),
    )


def _installed(root: Path, manager: str, names: frozenset[str] | None) -> list[InstalledTool]:
    if names is not None:
        return _targeted_installed(root, manager, names)
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


def _targeted_installed(root: Path, manager: str, names: frozenset[str]) -> list[InstalledTool]:
    by_app: dict[str, tuple[str, list[ToolEntrypoint]]] = {}
    for name in sorted(names):
        target = _shim_target(root / "shims" / f"{name}.shim")
        if target is None:
            continue
        app = _target_app(target)
        if app is None:
            continue
        _app, entrypoints = by_app.setdefault(app.casefold(), (app, []))
        entrypoints.append(ToolEntrypoint(name=name, path=_shim_executable(root, name), source="scoop-shim"))
    installed: list[InstalledTool] = []
    for _app_key, (app, entrypoints) in sorted(by_app.items()):
        app_dir = root / "apps" / app
        current = app_dir / "current"
        installed.append(
            InstalledTool(
                name=app,
                manager=manager,
                path=current if current.exists() else app_dir,
                scope="intentional",
                entrypoints=tuple(entrypoints),
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
        app = _target_app(target)
        if app is None:
            continue
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


def _target_app(target: Path) -> str | None:
    parts = [part.casefold() for part in target.parts]
    try:
        app_index = parts.index("apps") + 1
    except ValueError:
        return None
    if app_index >= len(target.parts):
        return None
    return target.parts[app_index]


def _shim_executable(root: Path, name: str) -> Path:
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        candidate = root / "shims" / f"{name}{suffix}"
        if candidate.exists():
            return candidate
    return root / "shims" / f"{name}.shim"
