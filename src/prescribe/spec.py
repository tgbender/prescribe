import ntpath
import os
import shutil
import socket
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import tomlkit

from prescribe.encoding import read_utf8_text

KNOWN_FORMATS = frozenset({"toml", "yaml", "jsonc", "line"})
KNOWN_PLATFORMS = frozenset({"linux", "macos", "windows", "wsl"})
KNOWN_SHELLS = frozenset({"xonsh", "bash", "zsh", "fish", "nu", "pwsh", "cmd"})
KNOWN_ASSET_MODES = frozenset({"file", "mirror"})
KNOWN_LOCATION_MODES = frozenset({"first_existing_parent", "first_existing", "first", "required", "create_parent"})
KNOWN_LOCATION_KINDS = frozenset({"file", "dir", "any"})
KNOWN_SECTION_KEYS = frozenset({"files", "env", "shell", "assets", "locations"})


class SpecError(Exception):
    pass


# ── target types ─────────────────────────────────────────


@dataclass(slots=True)
class LocationCandidate:
    path: Path
    platforms: list[str] = field(default_factory=list)
    machine: list[str] = field(default_factory=list)
    if_command_exists: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Location:
    name: str
    candidates: list[LocationCandidate]
    mode: str = "first_existing_parent"
    kind: str = "any"


@dataclass(slots=True)
class FileTarget:
    path: Path
    format: str
    priority: int = 0
    data: dict[str, Any] = field(default_factory=dict)
    delete: list[str] = field(default_factory=list)
    managed_block_id: str | None = None
    lines: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    machine: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    if_command_exists: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EnvTarget:
    name: str
    value: str | None = None
    prepend: list[str] = field(default_factory=list)
    append: list[str] = field(default_factory=list)
    path_prepend: list[str] = field(default_factory=list)
    path_append: list[str] = field(default_factory=list)
    materialize: bool = False
    platforms: list[str] = field(default_factory=list)
    machine: list[str] = field(default_factory=list)
    shells: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    if_command_exists: list[str] = field(default_factory=list)
    if_env_missing: bool = False


@dataclass(slots=True)
class ShellTarget:
    path: Path
    managed_block_id: str
    shells: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    machine: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AssetTarget:
    source: str
    dest: Path
    mode: str = "file"
    delete_extra: bool = False
    paths: list[Path] = field(default_factory=list)
    replace: bool = False
    max_displace_bytes: int = 10 * 1024 * 1024
    allow_binary: bool = False
    platforms: list[str] = field(default_factory=list)
    machine: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    if_command_exists: list[str] = field(default_factory=list)


# ── spec ──────────────────────────────────────────────────


@dataclass(slots=True)
class Spec:
    id: str | None = None
    path: Path = field(default_factory=Path)
    locations: dict[str, Location] = field(default_factory=dict)
    files: list[FileTarget] = field(default_factory=list)
    env: list[EnvTarget] = field(default_factory=list)
    shell: list[ShellTarget] = field(default_factory=list)
    assets: list[AssetTarget] = field(default_factory=list)


# ── loader ────────────────────────────────────────────────


class SpecLoader:
    def load(self, spec_path: Path) -> Spec:
        try:
            data = tomlkit.parse(read_utf8_text(spec_path))
        except Exception as exc:
            raise SpecError(f"failed to parse spec {spec_path}: {exc}") from exc

        if "targets" in data:
            raise SpecError(
                f"spec {spec_path}: [[targets]] is removed in v0.2.0. "
                "Use [[files]], [[env]], and [[shell]] sections instead."
            )

        # Resolve [vars] section first
        vars_dict = _resolve_vars_section(spec_path, data.get("vars", {}))

        locations = self._parse_locations(spec_path, data.get("locations", {}), vars_dict)
        files = self._parse_files(spec_path, data.get("files", []), vars_dict, locations)
        env_vars = self._parse_env(spec_path, data.get("env", []), vars_dict)
        shells = self._parse_shell(spec_path, data.get("shell", []), vars_dict, locations)
        assets = self._parse_assets(spec_path, data.get("assets", []), vars_dict, locations)

        _validate_no_duplicate_overlapping_files(spec_path, files)

        return Spec(path=spec_path, locations=locations, files=files, env=env_vars, shell=shells, assets=assets)

    # ── locations ─────────────────────────────────────

    def _parse_locations(self, spec_path: Path, raw_locations: Any, vars_dict: dict[str, str]) -> dict[str, Location]:
        if raw_locations is None:
            return {}
        if not isinstance(raw_locations, Mapping):
            raise SpecError(f"spec {spec_path}: 'locations' must be a table/object")
        result: dict[str, Location] = {}
        for raw_name, raw in raw_locations.items():
            name = str(raw_name)
            ctx = f"spec {spec_path} locations.{name}"
            if not name:
                raise SpecError(f"{ctx}: location name must be non-empty")
            if not isinstance(raw, Mapping):
                raise SpecError(f"{ctx}: expected a table/object")
            raw_candidates = raw.get("candidates")
            if not isinstance(raw_candidates, list) or not raw_candidates:
                raise SpecError(f"{ctx}: key 'candidates' must be a non-empty list")
            mode = _coerce_optional_string(raw.get("mode"), ctx, "mode") or "first_existing_parent"
            if mode not in KNOWN_LOCATION_MODES:
                raise SpecError(f"{ctx}: unknown mode {mode!r}, expected one of {sorted(KNOWN_LOCATION_MODES)}")
            kind = _coerce_optional_string(raw.get("kind"), ctx, "kind") or "any"
            if kind not in KNOWN_LOCATION_KINDS:
                raise SpecError(f"{ctx}: unknown kind {kind!r}, expected one of {sorted(KNOWN_LOCATION_KINDS)}")
            candidates = [
                _parse_location_candidate(spec_path, index, candidate, vars_dict, ctx)
                for index, candidate in enumerate(raw_candidates)
            ]
            result[name] = Location(name=name, candidates=candidates, mode=mode, kind=kind)
        return result

    # ── files ─────────────────────────────────────────

    def _parse_files(
        self,
        spec_path: Path,
        raw_list: Any,
        vars_dict: dict[str, str],
        locations: dict[str, Location],
    ) -> list[FileTarget]:
        if not isinstance(raw_list, list):
            raise SpecError(f"spec {spec_path}: 'files' must be an array of tables")
        targets: list[FileTarget] = []
        for index, raw in enumerate(raw_list):
            targets.append(self._parse_file_target(spec_path, index, raw, vars_dict, locations))
        return targets

    def _parse_file_target(
        self,
        spec_path: Path,
        index: int,
        raw: Any,
        vars_dict: dict[str, str],
        locations: dict[str, Location],
    ) -> FileTarget:
        ctx = f"spec {spec_path} files[{index}]"
        if not isinstance(raw, Mapping):
            raise SpecError(f"{ctx}: expected a table/object")

        fmt = _require_format(raw, ctx)

        if fmt == "line" and "managed_block_id" not in raw:
            raise SpecError(f"{ctx}: format 'line' requires 'managed_block_id'")
        if "lines" in raw and "text" in raw:
            raise SpecError(f"{ctx}: specify either 'lines' or 'text', not both")
        if "lines" in raw and "text_from" in raw:
            raise SpecError(f"{ctx}: specify either 'lines' or 'text_from', not both")
        if "text" in raw and "text_from" in raw:
            raise SpecError(f"{ctx}: specify either 'text' or 'text_from', not both")

        paths = [expand_spec_vars(p, vars_dict) for p in _coerce_string_list(raw.get("paths", []), ctx, "paths")]
        lines = _coerce_string_list(raw.get("lines", []), ctx, "lines")
        text = _coerce_optional_string(raw.get("text"), ctx, "text")
        text_from = _coerce_optional_string(raw.get("text_from"), ctx, "text_from")
        if text is not None:
            lines = _text_to_lines(text)
        if text_from is not None:
            text_path = _resolve_source_pattern(spec_path, expand_spec_vars(text_from, vars_dict))
            if _has_glob(str(text_path)):
                raise SpecError(f"{ctx}: text_from must not be a glob pattern")
            try:
                lines = _text_to_lines(read_utf8_text(text_path))
            except OSError as exc:
                raise SpecError(f"{ctx}: failed to read text_from {text_path}: {exc}") from exc
        return FileTarget(
            path=_resolve_target_from_spec(
                spec_path,
                raw,
                ctx,
                vars_dict,
                locations,
                path_key="path",
                location_key="location",
                append_key="path_append",
                fallback_paths=paths,
                destination=False,
            ),
            format=fmt,
            priority=_coerce_optional_int(raw.get("priority"), ctx, "priority", default=0),
            data=_coerce_mapping(raw.get("data", {}), ctx, "data"),
            delete=_coerce_string_list(raw.get("delete", []), ctx, "delete"),
            managed_block_id=_coerce_optional_string(raw.get("managed_block_id"), ctx, "managed_block_id"),
            lines=lines,
            platforms=_coerce_platforms(raw.get("platforms", []), ctx),
            machine=_coerce_string_list(raw.get("machine", []), ctx, "machine"),
            tags=_coerce_string_list(raw.get("tags", []), ctx, "tags"),
            if_command_exists=_coerce_string_list(raw.get("if_command_exists", []), ctx, "if_command_exists"),
        )

    # ── env ───────────────────────────────────────────

    def _parse_env(self, spec_path: Path, raw_list: Any, vars_dict: dict[str, str]) -> list[EnvTarget]:
        if not isinstance(raw_list, list):
            raise SpecError(f"spec {spec_path}: 'env' must be an array of tables")
        targets: list[EnvTarget] = []
        for index, raw in enumerate(raw_list):
            targets.append(self._parse_env_target(spec_path, index, raw, vars_dict))
        return targets

    def _parse_env_target(self, spec_path: Path, index: int, raw: Any, vars_dict: dict[str, str]) -> EnvTarget:
        ctx = f"spec {spec_path} env[{index}]"
        if not isinstance(raw, Mapping):
            raise SpecError(f"{ctx}: expected a table/object")

        name = _coerce_required_string(raw.get("name"), ctx, "name")

        raw_value = _coerce_optional_string(raw.get("value"), ctx, "value")

        def _expand_v(v: str) -> str:
            return _expand(expand_spec_vars(v, vars_dict))

        return EnvTarget(
            name=str(name),
            value=_expand_v(raw_value) if raw_value else None,
            prepend=[_expand_v(e) for e in _coerce_string_list(raw.get("prepend", []), ctx, "prepend")],
            append=[_expand_v(e) for e in _coerce_string_list(raw.get("append", []), ctx, "append")],
            path_prepend=[_expand_v(e) for e in _coerce_string_list(raw.get("path_prepend", []), ctx, "path_prepend")],
            path_append=[_expand_v(e) for e in _coerce_string_list(raw.get("path_append", []), ctx, "path_append")],
            materialize=bool(raw.get("materialize", False)),
            platforms=_coerce_platforms(raw.get("platforms", []), ctx),
            machine=_coerce_string_list(raw.get("machine", []), ctx, "machine"),
            shells=_coerce_shells(raw.get("shells", []), ctx),
            tags=_coerce_string_list(raw.get("tags", []), ctx, "tags"),
            if_command_exists=_coerce_string_list(raw.get("if_command_exists", []), ctx, "if_command_exists"),
            if_env_missing=bool(raw.get("if_env_missing", False)),
        )

    # ── shell ─────────────────────────────────────────

    def _parse_shell(
        self,
        spec_path: Path,
        raw_list: Any,
        vars_dict: dict[str, str],
        locations: dict[str, Location],
    ) -> list[ShellTarget]:
        if not isinstance(raw_list, list):
            raise SpecError(f"spec {spec_path}: 'shell' must be an array of tables")
        targets: list[ShellTarget] = []
        for index, raw in enumerate(raw_list):
            targets.append(self._parse_shell_target(spec_path, index, raw, vars_dict, locations))
        return targets

    def _parse_shell_target(
        self,
        spec_path: Path,
        index: int,
        raw: Any,
        vars_dict: dict[str, str],
        locations: dict[str, Location],
    ) -> ShellTarget:
        ctx = f"spec {spec_path} shell[{index}]"
        if not isinstance(raw, Mapping):
            raise SpecError(f"{ctx}: expected a table/object")

        block_id = _coerce_required_string(raw.get("managed_block_id"), ctx, "managed_block_id")

        return ShellTarget(
            path=_resolve_target_from_spec(
                spec_path,
                raw,
                ctx,
                vars_dict,
                locations,
                path_key="path",
                location_key="location",
                append_key="path_append",
                fallback_paths=[],
                destination=False,
            ),
            managed_block_id=str(block_id),
            shells=_coerce_shells(raw.get("shells", []), ctx),
            platforms=_coerce_platforms(raw.get("platforms", []), ctx),
            machine=_coerce_string_list(raw.get("machine", []), ctx, "machine"),
            tags=_coerce_string_list(raw.get("tags", []), ctx, "tags"),
        )

    # ── assets ────────────────────────────────────────

    def _parse_assets(
        self,
        spec_path: Path,
        raw_list: Any,
        vars_dict: dict[str, str],
        locations: dict[str, Location],
    ) -> list[AssetTarget]:
        if not isinstance(raw_list, list):
            raise SpecError(f"spec {spec_path}: 'assets' must be an array of tables")
        targets: list[AssetTarget] = []
        for index, raw in enumerate(raw_list):
            targets.append(self._parse_asset_target(spec_path, index, raw, vars_dict, locations))
        return targets

    def _parse_asset_target(
        self,
        spec_path: Path,
        index: int,
        raw: Any,
        vars_dict: dict[str, str],
        locations: dict[str, Location],
    ) -> AssetTarget:
        ctx = f"spec {spec_path} assets[{index}]"
        if not isinstance(raw, Mapping):
            raise SpecError(f"{ctx}: expected a table/object")

        source = _coerce_required_string(raw.get("source"), ctx, "source")
        mode = _coerce_optional_string(raw.get("mode"), ctx, "mode") or ("mirror" if _has_glob(source) else "file")
        if mode not in KNOWN_ASSET_MODES:
            raise SpecError(f"{ctx}: unknown mode {mode!r}, expected one of {sorted(KNOWN_ASSET_MODES)}")
        if bool(raw.get("delete_extra", False)):
            raise SpecError(f"{ctx}: delete_extra is not implemented yet")
        replace = bool(raw.get("replace", False))
        if replace and mode != "mirror":
            raise SpecError(f"{ctx}: replace is only supported with mode 'mirror'")
        max_displace_bytes = _coerce_optional_int(
            raw.get("max_displace_bytes"), ctx, "max_displace_bytes", default=10 * 1024 * 1024
        )
        if max_displace_bytes < 0:
            raise SpecError(f"{ctx}: key 'max_displace_bytes' must be non-negative")
        paths = [
            _resolve_destination_path(spec_path, expand_spec_vars(p, vars_dict))
            for p in _coerce_string_list(raw.get("paths", []), ctx, "paths")
        ]
        dest_path = _resolve_target_from_spec(
            spec_path,
            raw,
            ctx,
            vars_dict,
            locations,
            path_key="dest",
            location_key="dest_location",
            append_key="dest_append",
            fallback_paths=[str(path) for path in paths],
            destination=True,
        )

        return AssetTarget(
            source=str(_resolve_source_pattern(spec_path, expand_spec_vars(source, vars_dict))),
            dest=dest_path,
            mode=mode,
            delete_extra=bool(raw.get("delete_extra", False)),
            paths=paths,
            replace=replace,
            max_displace_bytes=max_displace_bytes,
            allow_binary=bool(raw.get("allow_binary", False)),
            platforms=_coerce_platforms(raw.get("platforms", []), ctx),
            machine=_coerce_string_list(raw.get("machine", []), ctx, "machine"),
            tags=_coerce_string_list(raw.get("tags", []), ctx, "tags"),
            if_command_exists=_coerce_string_list(raw.get("if_command_exists", []), ctx, "if_command_exists"),
        )


# ── helpers ───────────────────────────────────────────────


def _parse_location_candidate(
    spec_path: Path,
    index: int,
    raw: Any,
    vars_dict: dict[str, str],
    parent_ctx: str,
) -> LocationCandidate:
    ctx = f"{parent_ctx}.candidates[{index}]"
    if isinstance(raw, str):
        path = raw
        platforms: list[str] = []
        machine: list[str] = []
        if_command_exists: list[str] = []
    elif isinstance(raw, Mapping):
        path = _coerce_required_string(raw.get("path"), ctx, "path")
        platforms = _coerce_platforms(raw.get("platforms", []), ctx)
        machine = _coerce_string_list(raw.get("machine", []), ctx, "machine")
        if_command_exists = _coerce_string_list(raw.get("if_command_exists", []), ctx, "if_command_exists")
    else:
        raise SpecError(f"{ctx}: candidate must be a string or table/object")
    return LocationCandidate(
        path=_resolve_destination_path(spec_path, expand_spec_vars(path, vars_dict)),
        platforms=platforms,
        machine=machine,
        if_command_exists=if_command_exists,
    )


def _resolve_target_from_spec(
    spec_path: Path,
    raw: Mapping[str, Any],
    ctx: str,
    vars_dict: dict[str, str],
    locations: dict[str, Location],
    *,
    path_key: str,
    location_key: str,
    append_key: str,
    fallback_paths: list[str],
    destination: bool,
) -> Path:
    path = _coerce_optional_string(raw.get(path_key), ctx, path_key)
    location_name = _coerce_optional_string(raw.get(location_key), ctx, location_key)
    append = _coerce_optional_string(raw.get(append_key), ctx, append_key)
    if path and location_name:
        raise SpecError(f"{ctx}: specify either '{path_key}' or '{location_key}', not both")
    if append and not location_name:
        raise SpecError(f"{ctx}: '{append_key}' requires '{location_key}'")
    if location_name:
        if fallback_paths:
            raise SpecError(f"{ctx}: 'paths' cannot be used with '{location_key}'")
        location = locations.get(location_name)
        if location is None:
            raise SpecError(f"{ctx}: unknown location {location_name!r}")
        resolved = _resolve_location(spec_path, location, ctx)
        if append:
            resolved = _append_relative_path(spec_path, resolved, expand_spec_vars(append, vars_dict), ctx, append_key)
        return resolved
    if not path:
        raise SpecError(f"{ctx}: missing required key '{path_key}' or '{location_key}'")
    if append:
        raise SpecError(f"{ctx}: '{append_key}' requires '{location_key}'")
    expanded = expand_spec_vars(path, vars_dict)
    return (
        _resolve_asset_dest_path(spec_path, expanded, [_resolve_destination_path(spec_path, p) for p in fallback_paths])
        if destination
        else _resolve_target_path(spec_path, expanded, fallback_paths)
    )


def _resolve_location(spec_path: Path, location: Location, ctx: str) -> Path:
    candidates = [candidate.path for candidate in location.candidates if _location_candidate_matches(candidate)]
    if not candidates:
        raise SpecError(f"{ctx}: location {location.name!r} has no active candidates")
    if location.kind == "file":
        for candidate in candidates:
            if candidate.exists() and candidate.is_dir():
                raise SpecError(f"{ctx}: location {location.name!r} expected file but found directory: {candidate}")
    elif location.kind == "dir":
        for candidate in candidates:
            if candidate.exists() and not candidate.is_dir():
                raise SpecError(f"{ctx}: location {location.name!r} expected directory but found file: {candidate}")

    if location.mode in {"first", "create_parent"}:
        return candidates[0]
    if location.mode in {"first_existing", "required"}:
        for candidate in candidates:
            if candidate.exists() or candidate.is_symlink():
                return candidate
        raise SpecError(f"{ctx}: location {location.name!r} did not match an existing path")
    for candidate in candidates:
        if candidate.exists() or candidate.is_symlink():
            return candidate
    for candidate in candidates:
        if candidate.parent.exists():
            return candidate
    return candidates[0]


def _location_candidate_matches(candidate: LocationCandidate) -> bool:
    if not _platform_selectors_match(candidate.platforms):
        return False
    if not _machine_selectors_match(candidate.machine):
        return False
    return all(shutil.which(command) is not None for command in candidate.if_command_exists)


def _append_relative_path(spec_path: Path, base: Path, raw_append: str, ctx: str, field_name: str) -> Path:
    expanded = os.path.expandvars(_expand_user(raw_append))
    append = Path(expanded)
    if append.is_absolute():
        raise SpecError(f"{ctx}: key '{field_name}' must be a relative path")
    return (base / append).resolve()


def _require_path(spec_path: Path, raw: Mapping[str, Any], ctx: str) -> str:
    raw_path = raw.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise SpecError(f"{ctx}: missing required key 'path'")
    return raw_path


def _require_format(raw: Mapping[str, Any], ctx: str) -> str:
    raw_fmt = raw.get("format")
    if not isinstance(raw_fmt, str) or not raw_fmt:
        raise SpecError(f"{ctx}: missing required key 'format'")
    if raw_fmt not in KNOWN_FORMATS:
        raise SpecError(f"{ctx}: unknown format {raw_fmt!r}, expected one of {sorted(KNOWN_FORMATS)}")
    return raw_fmt


def _coerce_required_string(value: Any, ctx: str, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise SpecError(f"{ctx}: key '{field_name}' must be a non-empty string")
    return value


def _coerce_optional_string(value: Any, ctx: str, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SpecError(f"{ctx}: key '{field_name}' must be a string when provided")
    return value


def _coerce_optional_int(value: Any, ctx: str, field_name: str, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise SpecError(f"{ctx}: key '{field_name}' must be an integer when provided")
    return cast(int, value)


def _coerce_mapping(value: Any, context: str, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SpecError(f"{context}: key '{field_name}' must be a table/object")
    return {str(key): _normalize_toml_value(inner) for key, inner in value.items()}


def _coerce_string_list(value: Any, context: str, field_name: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        if value is None:
            return []
        raise SpecError(f"{context}: key '{field_name}' must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise SpecError(f"{context}: key '{field_name}' item #{index} must be a non-empty string")
        result.append(item)
    return result


def _coerce_platforms(value: Any, ctx: str) -> list[str]:
    platforms = _coerce_string_list(value, ctx, "platforms")
    for platform in platforms:
        if platform not in KNOWN_PLATFORMS:
            raise SpecError(f"{ctx}: unknown platform {platform!r}, expected one of {sorted(KNOWN_PLATFORMS)}")
    return platforms


def _coerce_shells(value: Any, ctx: str) -> list[str]:
    shells = _coerce_string_list(value, ctx, "shells")
    for shell in shells:
        if shell not in KNOWN_SHELLS:
            raise SpecError(f"{ctx}: unknown shell {shell!r}, expected one of {sorted(KNOWN_SHELLS)}")
    return shells


def _resolve_target_path(spec_path: Path, primary: str, additional_paths: list[str]) -> Path:
    candidates = [
        _resolve_candidate_path(spec_path, primary),
        *(_resolve_candidate_path(spec_path, path) for path in additional_paths),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    for candidate in candidates:
        if candidate.parent.exists():
            return candidate
    return candidates[0]


def _resolve_candidate_path(spec_path: Path, raw_path: str) -> Path:
    expanded = os.path.expandvars(_expand_user(raw_path))
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = spec_path.parent / candidate
    return candidate.resolve()


def _resolve_source_pattern(spec_path: Path, raw_path: str) -> Path:
    expanded = os.path.expandvars(_expand_user(raw_path))
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = spec_path.parent / candidate
    if _has_glob(str(candidate)):
        return candidate
    return candidate.resolve()


def _resolve_destination_path(spec_path: Path, raw_path: str) -> Path:
    expanded = os.path.expandvars(_expand_user(raw_path))
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = spec_path.parent / candidate
    return candidate.parent.resolve() / candidate.name


def _resolve_asset_dest_path(spec_path: Path, primary: str, additional_paths: list[Path]) -> Path:
    primary_path = _resolve_destination_path(spec_path, primary)
    candidates = [primary_path, *additional_paths]
    for candidate in candidates:
        if candidate.exists() or candidate.is_symlink():
            return candidate
    for candidate in candidates:
        if candidate.parent.exists():
            return candidate
    return primary_path


def _has_glob(value: str) -> bool:
    return any(char in value for char in "*?[")


def _text_to_lines(value: str) -> list[str]:
    return value.splitlines()


def _normalize_toml_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize_toml_value(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_normalize_toml_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_toml_value(item) for item in value]
    unwrap = getattr(value, "unwrap", None)
    if callable(unwrap):
        return _normalize_toml_value(unwrap())
    return value


def _validate_no_duplicate_overlapping_files(spec_path: Path, targets: list[FileTarget]) -> None:
    for index, target in enumerate(targets):
        target_identity = _portable_path_identity(target.path)
        for prev_idx in range(index):
            previous = targets[prev_idx]
            if target_identity != _portable_path_identity(previous.path):
                continue
            if not _file_targets_overlap(target, previous):
                continue
            if target.priority != previous.priority:
                continue
            if str(target.path) != str(previous.path):
                raise SpecError(
                    f"spec {spec_path} files[{index}]: portable path collision: "
                    f"{previous.path} and {target.path} refer to the same case-insensitive path"
                )
            raise SpecError(
                f"spec {spec_path} files[{index}]: duplicate path {target.path} overlaps with files[{prev_idx}]"
                f" (same priority {target.priority})"
            )


def _file_targets_overlap(left: FileTarget, right: FileTarget) -> bool:
    return _values_overlap(left.platforms, right.platforms) and _values_overlap(left.machine, right.machine)


def _portable_path_identity(path: Path) -> str:
    return ntpath.normcase(ntpath.normpath(str(path)))


def _values_overlap(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return True
    if "all" in left or "all" in right:
        return True
    return any(value in right for value in left)


def _platform_selectors_match(selectors: list[str]) -> bool:
    if not selectors:
        return True
    return any(_platform_selector_matches(selector) for selector in selectors)


def _platform_selector_matches(selector: str) -> bool:
    if selector == "macos":
        return sys.platform == "darwin"
    if selector == "windows":
        return sys.platform == "win32"
    if selector == "linux":
        return sys.platform.startswith("linux")
    if selector == "wsl":
        return sys.platform.startswith("linux") and _is_wsl()
    return False


def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False


def _machine_selectors_match(selectors: list[str]) -> bool:
    if not selectors:
        return True
    machine = os.environ.get("PRESCRIBE_MACHINE") or socket.gethostname().split(".")[0]
    return any(selector == machine or selector == "all" for selector in selectors)


# ── vars resolution ──────────────────────────────────────


def _resolve_vars_section(spec_path: Path, raw_vars: Any) -> dict[str, str]:
    """Resolve [vars] section into a flat dict. Vars can reference $HOME, etc."""
    if not isinstance(raw_vars, Mapping):
        if raw_vars:
            raise SpecError(f"spec {spec_path}: 'vars' must be a table/object")
        return {}

    result: dict[str, str] = {}
    for key, value in raw_vars.items():
        if not isinstance(key, str) or not key:
            raise SpecError(f"spec {spec_path}: vars key must be a non-empty string")
        if not isinstance(value, str):
            raise SpecError(f"spec {spec_path}: vars.{key} must be a string")
        # Expand ~ and process env vars ($HOME, etc.)
        result[str(key)] = _expand(os.path.expandvars(str(value)))
    return result


def expand_spec_vars(value: str, vars_dict: dict[str, str]) -> str:
    """Expand $VAR references in a string using vars_dict.

    Expands longest keys first to avoid prefix collisions (e.g., $A must not
    corrupt $AB when both vars are defined).
    """
    result = value
    for var_name in sorted(vars_dict, key=len, reverse=True):
        result = result.replace(f"${var_name}", vars_dict[var_name])
    return result


def _expand(value: str) -> str:
    """Expand ~ to home directory in a string."""
    if value == "~" or value.startswith("~/"):
        return str(_home_path() / value[2:]) if value.startswith("~/") else str(_home_path())
    return value


def _expand_user(value: str) -> str:
    if value == "~" or value.startswith("~/"):
        return str(_home_path() / value[2:]) if value.startswith("~/") else str(_home_path())
    return os.path.expanduser(value)


def _home_path() -> Path:
    return Path(os.environ.get("HOME") or Path.home())
