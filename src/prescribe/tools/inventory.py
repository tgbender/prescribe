"""Public inventory API for read-only tool inspection."""

from __future__ import annotations

from pathlib import Path

from prescribe.tools.common import (
    InspectionContext,
    candidate_paths,
    case_key,
    context,
    iter_executables,
    path_entries,
    path_sources,
    tool_name,
)
from prescribe.tools.managers import brew, bun, mise, pnpm, scoop, uv
from prescribe.tools.models import (
    InstalledTool,
    ManagerInspector,
    ToolCandidate,
    ToolInventory,
    ToolManager,
    ToolPathSource,
)

_DEFAULT_MANAGERS: tuple[ToolManager, ...] = ("uv", "bun", "pnpm", "mise", "scoop", "brew")
_MANAGER_RESOLVERS: dict[str, ManagerInspector] = {
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
    include_path: bool = False,
    include_transitive: bool = False,
    attribute_installed_executables: bool = False,
    backends: dict[str, ManagerInspector] | None = None,
    include_candidates: bool = True,
) -> ToolInventory:
    """Inspect tool-manager paths and executable candidates without subprocesses.

    ``names`` enables targeted lookup: only those executable names are checked in
    manager-owned candidate directories. When omitted, existing manager bin
    directories are scanned once and known manager install metadata is included.

    ``include_path`` is explicit opt-in for whole-PATH discovery and active
    executable resolution. The default follows manager env vars, XDG paths,
    config files, and metadata only.

    ``backends`` can override or add manager inspectors. Each backend owns its
    manager-specific metadata lookup and receives targeted ``names`` so it can
    avoid subprocesses and broad directory walks.

    ``attribute_installed_executables`` is retained for compatibility. Tool
    attribution now comes from manager metadata and backend-owned targeted
    search, not inventory-level recursive install scans.

    Set ``include_candidates=False`` for installed inventory only. That mode
    reads manager metadata but does not inspect executable sources or PATH.
    """

    ctx = context(env=env, home=home, platform=platform)
    selected = tuple(managers or _DEFAULT_MANAGERS)
    target_names = frozenset(names) if names is not None else None
    entries = path_entries(ctx) if include_path else []
    resolvers = dict(_MANAGER_RESOLVERS)
    if backends:
        resolvers.update(backends)

    sources: list[ToolPathSource] = []
    installed: list[InstalledTool] = []
    issues: list[str] = []
    for manager in selected:
        result = resolvers[manager](ctx, entries, target_names)
        if include_candidates:
            sources.extend(result.sources)
        installed.extend(result.installed)
        issues.extend(result.issues)
    if include_candidates and include_path:
        sources.extend(path_sources(entries))

    visible_installed = tuple(tool for tool in installed if include_transitive or tool.scope != "dependency")
    candidates = (
        _candidate_map(
            sources=sources,
            installed=visible_installed,
            path_entries=entries if include_path else [],
            names=target_names,
            ctx=ctx,
        )
        if include_candidates
        else {}
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


def inspect_installed_tools(
    *,
    managers: list[ToolManager] | tuple[ToolManager, ...] | set[ToolManager] | None = None,
    env: dict[str, str] | None = None,
    home: Path | str | None = None,
    platform: str | None = None,
    include_transitive: bool = False,
    backends: dict[str, ManagerInspector] | None = None,
) -> ToolInventory:
    """Inspect installed manager metadata without executable or PATH discovery."""

    return inspect_tool_paths(
        managers=managers,
        env=env,
        home=home,
        platform=platform,
        include_path=False,
        include_transitive=include_transitive,
        backends=backends,
        include_candidates=False,
    )


def _candidate_map(
    *,
    sources: list[ToolPathSource],
    installed: tuple[InstalledTool, ...],
    path_entries: list[Path],
    names: frozenset[str] | None,
    ctx: InspectionContext,
) -> dict[str, tuple[ToolCandidate, ...]]:
    by_path = {case_key(entry): index for index, entry in enumerate(path_entries)}
    active_by_name = _active_by_name(path_entries, names, ctx)
    installed_by_path = {case_key(tool.path): tool for tool in installed}
    installed_by_entrypoint = _installed_by_entrypoint(installed)
    candidates: dict[str, list[ToolCandidate]] = {}
    for source in sources:
        if not source.exists:
            continue
        for candidate in iter_executables(source.path, names, ctx):
            name = tool_name(candidate, ctx)
            active = case_key(candidate) == case_key(active_by_name.get(name, Path()))
            installed_tool = _nearest_installed(candidate, installed_by_path)
            if installed_tool is None and source.manager != "path":
                installed_tool = installed_by_entrypoint.get((source.manager, name.casefold()))
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


def _installed_by_entrypoint(installed: tuple[InstalledTool, ...]) -> dict[tuple[str, str], InstalledTool]:
    result: dict[tuple[str, str], InstalledTool] = {}
    for tool in installed:
        for entrypoint in tool.entrypoints:
            result.setdefault((tool.manager, entrypoint.name.casefold()), tool)
    return result


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
