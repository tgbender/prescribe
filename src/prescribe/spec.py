import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit

from prescribe.core.planner import DesiredState

KNOWN_FORMATS = frozenset({"toml", "yaml", "json5", "jsonc", "line"})
KNOWN_PLATFORMS = frozenset({"linux", "macos", "windows"})


class SpecError(Exception):
    pass


@dataclass(slots=True)
class SpecTarget:
    path: Path
    format: str
    data: dict[str, Any] = field(default_factory=dict)
    delete: list[str] = field(default_factory=list)
    managed_block_id: str | None = None
    lines: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    machine: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Spec:
    path: Path
    targets: list[SpecTarget] = field(default_factory=list)

    def desired_states(self) -> list[DesiredState]:
        return [
            DesiredState(
                path=target.path,
                format=target.format,
                data=target.data,
                delete=target.delete,
                managed_block_id=target.managed_block_id,
                lines=target.lines,
            )
            for target in self.targets
        ]


class SpecLoader:
    def load(self, path: Path) -> Spec:
        try:
            data = tomlkit.parse(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SpecError(f"failed to parse spec {path}: {exc}") from exc

        raw_targets = data.get("targets")
        if raw_targets is None:
            raise SpecError(f"spec {path} missing required key 'targets'")
        if not isinstance(raw_targets, list):
            raise SpecError(f"spec {path} key 'targets' must be a list")

        targets: list[SpecTarget] = []
        for index, raw in enumerate(raw_targets):
            target = self._parse_target(path, index, raw)
            for previous_index, previous in enumerate(targets):
                if target.path == previous.path and _targets_overlap(target, previous):
                    raise SpecError(
                        f"spec {path} target #{index}: duplicate path {target.path} "
                        f"overlaps with target #{previous_index}"
                    )
            targets.append(target)
        return Spec(path=path, targets=targets)

    def _parse_target(self, spec_path: Path, index: int, raw: Any) -> SpecTarget:
        context = f"spec {spec_path} target #{index}"
        if not isinstance(raw, Mapping):
            raise SpecError(f"{context}: expected a table/object")

        if "path" not in raw:
            raise SpecError(f"{context}: missing required key 'path'")
        if "format" not in raw:
            raise SpecError(f"{context}: missing required key 'format'")

        raw_path = raw["path"]
        if not isinstance(raw_path, str) or not raw_path:
            raise SpecError(f"{context}: key 'path' must be a non-empty string")
        raw_paths = raw.get("paths", [])
        additional_paths = _coerce_string_list(raw_paths, context, "paths") if "paths" in raw else []
        raw_format = raw["format"]
        if not isinstance(raw_format, str) or not raw_format:
            raise SpecError(f"{context}: key 'format' must be a non-empty string")

        fmt = raw_format
        if fmt not in KNOWN_FORMATS:
            raise SpecError(f"{context}: unknown format {fmt!r}, expected one of {sorted(KNOWN_FORMATS)}")

        target_path = _resolve_target_path(spec_path, raw_path, additional_paths)

        data = _coerce_mapping(raw.get("data", {}), context, "data")
        delete = _coerce_string_list(raw.get("delete", []), context, "delete")
        lines = _coerce_string_list(raw.get("lines", []), context, "lines")
        platforms = _coerce_string_list(raw.get("platforms", []), context, "platforms")
        for platform in platforms:
            if platform not in KNOWN_PLATFORMS:
                raise SpecError(f"{context}: unknown platform {platform!r}, expected one of {sorted(KNOWN_PLATFORMS)}")

        machine = _coerce_string_list(raw.get("machine", []), context, "machine")

        managed_block_id = raw.get("managed_block_id")
        if managed_block_id is not None and (not isinstance(managed_block_id, str) or not managed_block_id):
            raise SpecError(f"{context}: key 'managed_block_id' must be a non-empty string when provided")
        if fmt == "line" and managed_block_id is None:
            raise SpecError(f"{context}: format 'line' requires 'managed_block_id'")

        return SpecTarget(
            path=target_path,
            format=fmt,
            data=data,
            delete=delete,
            managed_block_id=managed_block_id,
            lines=lines,
            platforms=platforms,
            machine=machine,
        )


def _coerce_mapping(value: Any, context: str, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SpecError(f"{context}: key '{field_name}' must be a table/object")
    return {str(key): _normalize_toml_value(inner) for key, inner in value.items()}


def _coerce_string_list(value: Any, context: str, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise SpecError(f"{context}: key '{field_name}' must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise SpecError(f"{context}: key '{field_name}' item #{index} must be a non-empty string")
        result.append(item)
    return result


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


def _targets_overlap(left: SpecTarget, right: SpecTarget) -> bool:
    return _values_overlap(left.platforms, right.platforms) and _values_overlap(left.machine, right.machine)


def _values_overlap(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return True
    if "all" in left or "all" in right:
        return True
    return any(value in right for value in left)
