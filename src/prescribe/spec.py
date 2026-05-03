import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit

KNOWN_FORMATS = frozenset({"toml", "yaml", "jsonc", "line"})
KNOWN_PLATFORMS = frozenset({"linux", "macos", "windows"})
KNOWN_SHELLS = frozenset({"xonsh", "bash", "zsh", "fish", "nu"})
KNOWN_SECTION_KEYS = frozenset({"files", "env", "shell"})


class SpecError(Exception):
    pass


# ── target types ─────────────────────────────────────────


@dataclass(slots=True)
class FileTarget:
    path: Path
    format: str
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


# ── spec ──────────────────────────────────────────────────


@dataclass(slots=True)
class Spec:
    id: str | None = None
    path: Path = field(default_factory=Path)
    files: list[FileTarget] = field(default_factory=list)
    env: list[EnvTarget] = field(default_factory=list)
    shell: list[ShellTarget] = field(default_factory=list)


# ── loader ────────────────────────────────────────────────


class SpecLoader:
    def load(self, spec_path: Path) -> Spec:
        try:
            data = tomlkit.parse(spec_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SpecError(f"failed to parse spec {spec_path}: {exc}") from exc

        if "targets" in data:
            raise SpecError(
                f"spec {spec_path}: [[targets]] is removed in v0.2.0. "
                "Use [[files]], [[env]], and [[shell]] sections instead."
            )

        files = self._parse_files(spec_path, data.get("files", []))
        env_vars = self._parse_env(spec_path, data.get("env", []))
        shells = self._parse_shell(spec_path, data.get("shell", []))

        _validate_no_duplicate_overlapping_files(spec_path, files)

        return Spec(path=spec_path, files=files, env=env_vars, shell=shells)

    # ── files ─────────────────────────────────────────

    def _parse_files(self, spec_path: Path, raw_list: Any) -> list[FileTarget]:
        if not isinstance(raw_list, list):
            raise SpecError(f"spec {spec_path}: 'files' must be an array of tables")
        targets: list[FileTarget] = []
        for index, raw in enumerate(raw_list):
            targets.append(self._parse_file_target(spec_path, index, raw))
        return targets

    def _parse_file_target(self, spec_path: Path, index: int, raw: Any) -> FileTarget:
        ctx = f"spec {spec_path} files[{index}]"
        if not isinstance(raw, Mapping):
            raise SpecError(f"{ctx}: expected a table/object")

        path = _require_path(spec_path, raw, ctx)
        fmt = _require_format(raw, ctx)

        if fmt == "line" and "managed_block_id" not in raw:
            raise SpecError(f"{ctx}: format 'line' requires 'managed_block_id'")

        return FileTarget(
            path=_resolve_target_path(spec_path, path, _coerce_string_list(raw.get("paths", []), ctx, "paths")),
            format=fmt,
            data=_coerce_mapping(raw.get("data", {}), ctx, "data"),
            delete=_coerce_string_list(raw.get("delete", []), ctx, "delete"),
            managed_block_id=_coerce_optional_string(raw.get("managed_block_id"), ctx, "managed_block_id"),
            lines=_coerce_string_list(raw.get("lines", []), ctx, "lines"),
            platforms=_coerce_platforms(raw.get("platforms", []), ctx),
            machine=_coerce_string_list(raw.get("machine", []), ctx, "machine"),
            tags=_coerce_string_list(raw.get("tags", []), ctx, "tags"),
            if_command_exists=_coerce_string_list(raw.get("if_command_exists", []), ctx, "if_command_exists"),
        )

    # ── env ───────────────────────────────────────────

    def _parse_env(self, spec_path: Path, raw_list: Any) -> list[EnvTarget]:
        if not isinstance(raw_list, list):
            raise SpecError(f"spec {spec_path}: 'env' must be an array of tables")
        targets: list[EnvTarget] = []
        for index, raw in enumerate(raw_list):
            targets.append(self._parse_env_target(spec_path, index, raw))
        return targets

    def _parse_env_target(self, spec_path: Path, index: int, raw: Any) -> EnvTarget:
        ctx = f"spec {spec_path} env[{index}]"
        if not isinstance(raw, Mapping):
            raise SpecError(f"{ctx}: expected a table/object")

        name = _coerce_required_string(raw.get("name"), ctx, "name")

        return EnvTarget(
            name=str(name),
            value=_coerce_optional_string(raw.get("value"), ctx, "value"),
            prepend=_coerce_string_list(raw.get("prepend", []), ctx, "prepend"),
            append=_coerce_string_list(raw.get("append", []), ctx, "append"),
            path_prepend=_coerce_string_list(raw.get("path_prepend", []), ctx, "path_prepend"),
            path_append=_coerce_string_list(raw.get("path_append", []), ctx, "path_append"),
            materialize=bool(raw.get("materialize", False)),
            platforms=_coerce_platforms(raw.get("platforms", []), ctx),
            machine=_coerce_string_list(raw.get("machine", []), ctx, "machine"),
            shells=_coerce_shells(raw.get("shells", []), ctx),
            tags=_coerce_string_list(raw.get("tags", []), ctx, "tags"),
            if_command_exists=_coerce_string_list(raw.get("if_command_exists", []), ctx, "if_command_exists"),
            if_env_missing=bool(raw.get("if_env_missing", False)),
        )

    # ── shell ─────────────────────────────────────────

    def _parse_shell(self, spec_path: Path, raw_list: Any) -> list[ShellTarget]:
        if not isinstance(raw_list, list):
            raise SpecError(f"spec {spec_path}: 'shell' must be an array of tables")
        targets: list[ShellTarget] = []
        for index, raw in enumerate(raw_list):
            targets.append(self._parse_shell_target(spec_path, index, raw))
        return targets

    def _parse_shell_target(self, spec_path: Path, index: int, raw: Any) -> ShellTarget:
        ctx = f"spec {spec_path} shell[{index}]"
        if not isinstance(raw, Mapping):
            raise SpecError(f"{ctx}: expected a table/object")

        path = _require_path(spec_path, raw, ctx)
        block_id = _coerce_required_string(raw.get("managed_block_id"), ctx, "managed_block_id")

        return ShellTarget(
            path=_resolve_target_path(spec_path, path, []),
            managed_block_id=str(block_id),
            shells=_coerce_shells(raw.get("shells", []), ctx),
            platforms=_coerce_platforms(raw.get("platforms", []), ctx),
            machine=_coerce_string_list(raw.get("machine", []), ctx, "machine"),
            tags=_coerce_string_list(raw.get("tags", []), ctx, "tags"),
        )


# ── helpers ───────────────────────────────────────────────


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
    expanded = os.path.expandvars(os.path.expanduser(raw_path))
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = spec_path.parent / candidate
    return candidate.resolve()


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
        for prev_idx in range(index):
            previous = targets[prev_idx]
            if target.path == previous.path and _file_targets_overlap(target, previous):
                raise SpecError(
                    f"spec {spec_path} files[{index}]: duplicate path {target.path} overlaps with files[{prev_idx}]"
                )


def _file_targets_overlap(left: FileTarget, right: FileTarget) -> bool:
    return _values_overlap(left.platforms, right.platforms) and _values_overlap(left.machine, right.machine)


def _values_overlap(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return True
    if "all" in left or "all" in right:
        return True
    return any(value in right for value in left)
