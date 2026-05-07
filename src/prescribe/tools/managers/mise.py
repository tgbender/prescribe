"""mise path inspection backend."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from prescribe.tools.common import (
    InspectionContext,
    candidate_paths,
    env_path,
    is_executable,
    iter_dirs,
    read_toml,
    source,
    xdg_data,
)
from prescribe.tools.models import InstalledTool, ManagerResult, ToolEntrypoint

_KNOWN_ENTRYPOINTS: dict[str, tuple[str, ...]] = {
    "atuin": ("atuin",),
    "bun": ("bun",),
    "eza": ("eza",),
    "mypy": ("mypy", "dmypy", "stubgen", "stubtest", "mypyc"),
    "prek": ("prek",),
    "ripgrep": ("rg",),
    "ruff": ("ruff",),
    "starship": ("starship",),
    "taplo": ("taplo",),
    "trufflehog": ("trufflehog",),
    "uv": ("uv", "uvx"),
    "zoxide": ("zoxide", "z"),
}


class MiseToolSearch(Protocol):
    def __call__(
        self,
        install: MiseToolInstall,
        names: frozenset[str] | None,
        ctx: InspectionContext,
    ) -> tuple[ToolEntrypoint, ...]: ...


@dataclass(frozen=True)
class MiseToolInstall:
    """Resolved mise install metadata passed to tool-specific search backends."""

    name: str
    path: Path
    version: str | None


_TOOL_SEARCHERS: dict[str, MiseToolSearch] = {}


def register_tool_search(name: str, search: MiseToolSearch) -> None:
    """Register a mise tool-aware entrypoint search backend."""

    _TOOL_SEARCHERS[name] = search


def inspect(ctx: InspectionContext, path_entries: list[Path], names: frozenset[str] | None = None) -> ManagerResult:
    data = env_path(ctx, "MISE_DATA_DIR") or xdg_data(ctx) / "mise"
    installs = env_path(ctx, "MISE_INSTALLS_DIR") or data / "installs"
    shims = env_path(ctx, "MISE_SHIMS_DIR") or data / "shims"
    return ManagerResult(
        sources=(source("mise", "shims", shims, path_entries),),
        installed=tuple(_installed(installs, ctx, names)),
    )


def _installed(installs: Path, ctx: InspectionContext, names: frozenset[str] | None) -> list[InstalledTool]:
    manifest = read_toml(installs / ".mise-installs.toml")
    configured_versions = _configured_versions(ctx)
    installed: list[InstalledTool] = []
    if isinstance(manifest, dict):
        for folder, info in manifest.items():
            if not isinstance(info, dict):
                continue
            short = str(info.get("short") or info.get("full") or folder)
            name = _short_name(short)
            path = installs / folder
            version = configured_versions.get(short) or configured_versions.get(name)
            install = MiseToolInstall(name=name, path=path, version=version)
            installed.append(
                InstalledTool(
                    name=name,
                    manager="mise",
                    path=path,
                    scope="intentional",
                    version=version,
                    entrypoints=_tool_entrypoints(install, names, ctx),
                )
            )
    known = {str(tool.path).casefold() for tool in installed}
    for path in iter_dirs(installs):
        if path.name.startswith(".") or path.name == "incomplete" or str(path).casefold() in known:
            continue
        version = configured_versions.get(path.name)
        install = MiseToolInstall(name=path.name, path=path, version=version)
        installed.append(
            InstalledTool(
                name=path.name,
                manager="mise",
                path=path,
                scope="unknown",
                version=version,
                entrypoints=_tool_entrypoints(install, names, ctx),
            )
        )
    return installed


def _short_name(value: str) -> str:
    return value.rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def _tool_entrypoints(
    install: MiseToolInstall, names: frozenset[str] | None, ctx: InspectionContext
) -> tuple[ToolEntrypoint, ...]:
    search = _TOOL_SEARCHERS.get(install.name)
    if search is not None:
        return search(install, names, ctx)
    return _known_entrypoints(install, names, ctx)


def _known_entrypoints(
    install: MiseToolInstall, names: frozenset[str] | None, ctx: InspectionContext
) -> tuple[ToolEntrypoint, ...]:
    configured = _KNOWN_ENTRYPOINTS.get(install.name, (install.name,))
    requested = _requested_entrypoints(configured, names)
    if not requested:
        return ()
    version_roots = _version_roots(install.path, version=install.version)
    entrypoints: list[ToolEntrypoint] = []
    for name in requested:
        path = _find_entrypoint(version_roots, name, ctx)
        if path is not None:
            entrypoints.append(ToolEntrypoint(name=name, path=path, source="mise-install"))
    return tuple(entrypoints)


def _requested_entrypoints(configured: tuple[str, ...], names: frozenset[str] | None) -> tuple[str, ...]:
    if names is None:
        return ()
    return tuple(name for name in configured if name in names)


def _version_roots(root: Path, *, version: str | None) -> tuple[Path, ...]:
    selected: list[Path] = []
    if version:
        configured = _resolve_pointer(root / version)
        if configured is not None:
            selected.append(configured)
    current = _resolve_pointer(root / "current")
    if current is not None:
        selected.append(current)
    latest = _resolve_pointer(root / "latest")
    if latest is not None and latest not in selected:
        selected.append(latest)
    if not selected:
        versions = [path for path in iter_dirs(root) if not path.name.startswith(".")]
        if versions:
            selected.append(versions[-1])
    return tuple(selected)


def _configured_versions(ctx: InspectionContext) -> dict[str, str]:
    config = read_toml(_global_config_path(ctx))
    if not isinstance(config, dict):
        return {}
    tools = config.get("tools")
    if not isinstance(tools, dict):
        return {}
    versions: dict[str, str] = {}
    for key, value in tools.items():
        version = _configured_version(value)
        if version:
            versions[str(key)] = version
    return versions


def _global_config_path(ctx: InspectionContext) -> Path:
    if value := env_path(ctx, "MISE_GLOBAL_CONFIG_FILE"):
        return value
    config_home = env_path(ctx, "XDG_CONFIG_HOME") or ctx.home / ".config"
    return config_home / "mise" / "config.toml"


def _configured_version(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        return str(value[0])
    return None


def _resolve_pointer(path: Path) -> Path | None:
    if path.is_dir():
        return path
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    target = Path(raw)
    if not target.is_absolute():
        target = path.parent / target
    return target.resolve(strict=False)


def _find_entrypoint(roots: tuple[Path, ...], name: str, ctx: InspectionContext) -> Path | None:
    for root in roots:
        found = _find_entrypoint_at_depth(root, name, ctx, depth=0)
        if found is not None:
            return found
    return None


def _find_entrypoint_at_depth(root: Path, name: str, ctx: InspectionContext, *, depth: int) -> Path | None:
    for candidate in candidate_paths(root, name, ctx):
        try:
            if candidate.is_file() and is_executable(candidate, ctx):
                return candidate.resolve(strict=False)
        except OSError:
            continue
    if depth >= 2:
        return None
    try:
        children = list(root.iterdir())
    except OSError:
        return None
    for child in children:
        try:
            if child.is_dir() and not child.name.startswith("."):
                found = _find_entrypoint_at_depth(child, name, ctx, depth=depth + 1)
                if found is not None:
                    return found
        except OSError:
            continue
    return None
