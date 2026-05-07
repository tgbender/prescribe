"""Public inventory API for read-only tool inspection."""

from __future__ import annotations

from pathlib import Path

from prescribe.tools.common import (
    InspectionContext,
    candidate_paths,
    case_key,
    context,
    is_executable,
    iter_executables,
    path_entries,
    path_sources,
    tool_name,
)
from prescribe.tools.managers import brew, bun, mise, pnpm, scoop, uv
from prescribe.tools.models import InstalledTool, ToolCandidate, ToolInventory, ToolManager, ToolPathSource

_DEFAULT_MANAGERS: tuple[ToolManager, ...] = ("uv", "bun", "pnpm", "mise", "scoop", "brew")
_MANAGER_RESOLVERS = {
    "uv": uv.inspect,
    "bun": bun.inspect,
    "pnpm": pnpm.inspect,
    "mise": mise.inspect,
    "scoop": scoop.inspect,
    "brew": brew.inspect,
}


def inspect_tool_paths(
    names: list[str] | tuple[str, ...] | set[str] | None = None,
    *,
    managers: list[ToolManager] | tuple[ToolManager, ...] | set[ToolManager] | None = None,
    env: dict[str, str] | None = None,
    home: Path | str | None = None,
    platform: str | None = None,
    include_path: bool = True,
    include_transitive: bool = False,
    attribute_installed_executables: bool = False,
) -> ToolInventory:
    """Inspect tool-manager paths and executable candidates without subprocesses.

    ``names`` enables targeted lookup: only those executable names are checked in
    candidate bin directories. When omitted, existing bin directories are scanned
    once and known manager install metadata is included.

    ``attribute_installed_executables`` performs bounded recursive searches under
    installed manager roots to attribute shim candidates like ``rg`` back to an
    installed package like ``ripgrep``. It is useful for explanations, but it is
    deliberately opt-in because some manager installs contain large virtualenvs
    or package trees.
    """

    ctx = context(env=env, home=home, platform=platform)
    selected = tuple(managers or _DEFAULT_MANAGERS)
    target_names = frozenset(names) if names is not None else None
    entries = path_entries(ctx)

    sources: list[ToolPathSource] = []
    installed: list[InstalledTool] = []
    issues: list[str] = []
    for manager in selected:
        result = _MANAGER_RESOLVERS[manager](ctx, entries)
        sources.extend(result.sources)
        installed.extend(result.installed)
        issues.extend(result.issues)
    if include_path:
        sources.extend(path_sources(entries))

    visible_installed = tuple(tool for tool in installed if include_transitive or tool.scope != "dependency")
    candidates = _candidate_map(
        sources=sources,
        installed=visible_installed,
        path_entries=entries,
        names=target_names,
        ctx=ctx,
        attribute_installed_executables=attribute_installed_executables,
    )
    duplicates = {name: values for name, values in candidates.items() if len(values) > 1}
    return ToolInventory(
        path_entries=tuple(entries),
        sources=tuple(sources),
        installed=visible_installed,
        tools=candidates,
        duplicates=duplicates,
        issues=tuple(issues),
    )


def _candidate_map(
    *,
    sources: list[ToolPathSource],
    installed: tuple[InstalledTool, ...],
    path_entries: list[Path],
    names: frozenset[str] | None,
    ctx: InspectionContext,
    attribute_installed_executables: bool,
) -> dict[str, tuple[ToolCandidate, ...]]:
    by_path = {case_key(entry): index for index, entry in enumerate(path_entries)}
    active_by_name = _active_by_name(path_entries, names, ctx)
    installed_by_path = {case_key(tool.path): tool for tool in installed}
    executable_cache: dict[tuple[str, str], InstalledTool | None] = {}
    candidates: dict[str, list[ToolCandidate]] = {}
    for source in sources:
        if not source.exists:
            continue
        for candidate in iter_executables(source.path, names, ctx):
            name = tool_name(candidate, ctx)
            active = case_key(candidate) == case_key(active_by_name.get(name, Path()))
            installed_tool = _nearest_installed(candidate, installed_by_path)
            if installed_tool is None and attribute_installed_executables and source.manager != "path":
                installed_tool = _find_installed_by_executable(
                    installed=installed,
                    manager=source.manager,
                    name=name,
                    ctx=ctx,
                    cache=executable_cache,
                )
            source_label = (
                source.manager if source.manager != "path" else f"path[{by_path.get(case_key(source.path), -1)}]"
            )
            candidates.setdefault(name, []).append(
                ToolCandidate(
                    name=name,
                    path=candidate,
                    source=source_label,
                    active=active,
                    scope=installed_tool.scope if installed_tool is not None else ("active" if active else "candidate"),
                    installed_name=installed_tool.name if installed_tool is not None else None,
                )
            )
    return {name: tuple(_dedupe_candidates(values)) for name, values in sorted(candidates.items())}


def _find_installed_by_executable(
    *,
    installed: tuple[InstalledTool, ...],
    manager: str,
    name: str,
    ctx: InspectionContext,
    cache: dict[tuple[str, str], InstalledTool | None],
) -> InstalledTool | None:
    key = (manager, name)
    if key in cache:
        return cache[key]
    for tool in installed:
        if tool.manager != manager:
            continue
        if _installed_contains_executable(tool.path, name, ctx):
            cache[key] = tool
            return tool
    cache[key] = None
    return None


def _installed_contains_executable(root: Path, name: str, ctx: InspectionContext) -> bool:
    stack = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        for candidate in candidate_paths(directory, name, ctx):
            if candidate.is_file() and is_executable(candidate, ctx):
                return True
        if depth >= 4:
            continue
        try:
            children = list(directory.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and not child.name.startswith("."):
                stack.append((child, depth + 1))
    return False


def _active_by_name(path_entries: list[Path], names: frozenset[str] | None, ctx: InspectionContext) -> dict[str, Path]:
    found: dict[str, Path] = {}
    if names is not None:
        for directory in path_entries:
            for name in names:
                if name in found:
                    continue
                for candidate in candidate_paths(directory, name, ctx):
                    if candidate.is_file():
                        found[name] = candidate.resolve(strict=False)
                        break
        return found
    for directory in path_entries:
        for candidate in iter_executables(directory, None, ctx):
            found.setdefault(tool_name(candidate, ctx), candidate)
    return found


def _nearest_installed(path: Path, installed_by_path: dict[str, InstalledTool]) -> InstalledTool | None:
    current = path
    while True:
        match = installed_by_path.get(case_key(current))
        if match is not None:
            return match
        if current.parent == current:
            return None
        current = current.parent


def _dedupe_candidates(values: list[ToolCandidate]) -> list[ToolCandidate]:
    seen: set[str] = set()
    result: list[ToolCandidate] = []
    for value in values:
        key = case_key(value.path)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
