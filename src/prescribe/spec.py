import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import tomlkit

KNOWN_FORMATS = frozenset({"toml", "yaml", "jsonc", "line"})
KNOWN_PLATFORMS = frozenset({"linux", "macos", "windows"})
KNOWN_SHELLS = frozenset({"xonsh", "bash", "zsh", "fish", "nu", "pwsh", "cmd"})
KNOWN_ASSET_MODES = frozenset({"file", "mirror"})
KNOWN_SECTION_KEYS = frozenset({"files", "env", "shell", "assets"})


class SpecError(Exception):
    pass


# ── target types ─────────────────────────────────────────


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
    platforms: list[str] = field(default_factory=list)
    machine: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    if_command_exists: list[str] = field(default_factory=list)


# ── spec ──────────────────────────────────────────────────


@dataclass(slots=True)
class Spec:
    id: str | None = None
    path: Path = field(default_factory=Path)
    files: list[FileTarget] = field(default_factory=list)
    env: list[EnvTarget] = field(default_factory=list)
    shell: list[ShellTarget] = field(default_factory=list)
    assets: list[AssetTarget] = field(default_factory=list)


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

        # Resolve [vars] section first
        vars_dict = _resolve_vars_section(spec_path, data.get("vars", {}))

        files = self._parse_files(spec_path, data.get("files", []), vars_dict)
        env_vars = self._parse_env(spec_path, data.get("env", []), vars_dict)
        shells = self._parse_shell(spec_path, data.get("shell", []), vars_dict)
        assets = self._parse_assets(spec_path, data.get("assets", []), vars_dict)

        _validate_no_duplicate_overlapping_files(spec_path, files)

        return Spec(path=spec_path, files=files, env=env_vars, shell=shells, assets=assets)

    # ── files ─────────────────────────────────────────

    def _parse_files(self, spec_path: Path, raw_list: Any, vars_dict: dict[str, str]) -> list[FileTarget]:
        if not isinstance(raw_list, list):
            raise SpecError(f"spec {spec_path}: 'files' must be an array of tables")
        targets: list[FileTarget] = []
        for index, raw in enumerate(raw_list):
            targets.append(self._parse_file_target(spec_path, index, raw, vars_dict))
        return targets

    def _parse_file_target(self, spec_path: Path, index: int, raw: Any, vars_dict: dict[str, str]) -> FileTarget:
        ctx = f"spec {spec_path} files[{index}]"
        if not isinstance(raw, Mapping):
            raise SpecError(f"{ctx}: expected a table/object")

        path = _require_path(spec_path, raw, ctx)
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
                lines = _text_to_lines(text_path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise SpecError(f"{ctx}: failed to read text_from {text_path}: {exc}") from exc
        return FileTarget(
            path=_resolve_target_path(spec_path, expand_spec_vars(path, vars_dict), paths),
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

    def _parse_shell(self, spec_path: Path, raw_list: Any, vars_dict: dict[str, str]) -> list[ShellTarget]:
        if not isinstance(raw_list, list):
            raise SpecError(f"spec {spec_path}: 'shell' must be an array of tables")
        targets: list[ShellTarget] = []
        for index, raw in enumerate(raw_list):
            targets.append(self._parse_shell_target(spec_path, index, raw, vars_dict))
        return targets

    def _parse_shell_target(self, spec_path: Path, index: int, raw: Any, vars_dict: dict[str, str]) -> ShellTarget:
        ctx = f"spec {spec_path} shell[{index}]"
        if not isinstance(raw, Mapping):
            raise SpecError(f"{ctx}: expected a table/object")

        path = _require_path(spec_path, raw, ctx)
        block_id = _coerce_required_string(raw.get("managed_block_id"), ctx, "managed_block_id")

        return ShellTarget(
            path=_resolve_target_path(spec_path, expand_spec_vars(path, vars_dict), []),
            managed_block_id=str(block_id),
            shells=_coerce_shells(raw.get("shells", []), ctx),
            platforms=_coerce_platforms(raw.get("platforms", []), ctx),
            machine=_coerce_string_list(raw.get("machine", []), ctx, "machine"),
            tags=_coerce_string_list(raw.get("tags", []), ctx, "tags"),
        )

    # ── assets ────────────────────────────────────────

    def _parse_assets(self, spec_path: Path, raw_list: Any, vars_dict: dict[str, str]) -> list[AssetTarget]:
        if not isinstance(raw_list, list):
            raise SpecError(f"spec {spec_path}: 'assets' must be an array of tables")
        targets: list[AssetTarget] = []
        for index, raw in enumerate(raw_list):
            targets.append(self._parse_asset_target(spec_path, index, raw, vars_dict))
        return targets

    def _parse_asset_target(self, spec_path: Path, index: int, raw: Any, vars_dict: dict[str, str]) -> AssetTarget:
        ctx = f"spec {spec_path} assets[{index}]"
        if not isinstance(raw, Mapping):
            raise SpecError(f"{ctx}: expected a table/object")

        source = _coerce_required_string(raw.get("source"), ctx, "source")
        dest = _coerce_required_string(raw.get("dest"), ctx, "dest")
        mode = _coerce_optional_string(raw.get("mode"), ctx, "mode") or ("mirror" if _has_glob(source) else "file")
        if mode not in KNOWN_ASSET_MODES:
            raise SpecError(f"{ctx}: unknown mode {mode!r}, expected one of {sorted(KNOWN_ASSET_MODES)}")
        if bool(raw.get("delete_extra", False)):
            raise SpecError(f"{ctx}: delete_extra is not implemented yet")

        return AssetTarget(
            source=str(_resolve_source_pattern(spec_path, expand_spec_vars(source, vars_dict))),
            dest=_resolve_destination_path(spec_path, expand_spec_vars(dest, vars_dict)),
            mode=mode,
            delete_extra=bool(raw.get("delete_extra", False)),
            platforms=_coerce_platforms(raw.get("platforms", []), ctx),
            machine=_coerce_string_list(raw.get("machine", []), ctx, "machine"),
            tags=_coerce_string_list(raw.get("tags", []), ctx, "tags"),
            if_command_exists=_coerce_string_list(raw.get("if_command_exists", []), ctx, "if_command_exists"),
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
        for prev_idx in range(index):
            previous = targets[prev_idx]
            if target.path != previous.path:
                continue
            if not _file_targets_overlap(target, previous):
                continue
            if target.priority != previous.priority:
                continue
            raise SpecError(
                f"spec {spec_path} files[{index}]: duplicate path {target.path} overlaps with files[{prev_idx}]"
                f" (same priority {target.priority})"
            )


def _file_targets_overlap(left: FileTarget, right: FileTarget) -> bool:
    return _values_overlap(left.platforms, right.platforms) and _values_overlap(left.machine, right.machine)


def _values_overlap(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return True
    if "all" in left or "all" in right:
        return True
    return any(value in right for value in left)


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
