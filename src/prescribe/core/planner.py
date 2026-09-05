from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prescribe._util import _MISSING, mapping_key_parts, mapping_value


@dataclass(slots=True)
class PlannedOperation:
    kind: str
    path: Path
    key: str | None = None
    value: Any = None
    before_value: Any = None
    before_exists: bool | None = None
    reason: str | None = None
    before_key_parts: list[str] | None = None


@dataclass(slots=True)
class DesiredState:
    path: Path
    format: str
    data: dict[str, Any] = field(default_factory=dict)
    delete: list[str] = field(default_factory=list)
    managed_block_id: str | None = None
    lines: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PlanResult:
    changed: bool
    operations: list[PlannedOperation] = field(default_factory=list)


class Planner:
    def plan(self, current: Any, desired: DesiredState) -> PlanResult:
        format_name = getattr(current, "format", None)
        if format_name in {"toml", "yaml", "jsonc"}:
            return self._plan_mapping(current.root, desired)
        if format_name == "line":
            return self._plan_line(current.root, desired)
        raise ValueError(f"unsupported document format: {format_name}")

    def _plan_mapping(self, root: Any, desired: DesiredState) -> PlanResult:
        if root is None:
            root = {}
        operations: list[PlannedOperation] = []
        self._diff_mapping(root, desired.data, [], desired.path, operations)
        self._plan_deletions(root, desired.delete, desired.path, operations)
        return PlanResult(changed=len(operations) > 0, operations=operations)

    def _diff_mapping(
        self,
        current: Any,
        desired: dict[str, Any],
        prefix: list[str],
        path: Path,
        operations: list[PlannedOperation],
    ) -> None:
        for key, value in desired.items():
            dotted_key = ".".join([*prefix, key]) if prefix else key
            # At the top level, resolve dotted keys via mapping_value
            # (which handles flat-key fallback for formats like JSONC).
            if not prefix and "." in key:
                current_value = mapping_value(current, key)
                if current_value is _MISSING:
                    operations.append(
                        PlannedOperation(
                            kind="set",
                            path=path,
                            key=dotted_key,
                            value=value,
                            before_value=None,
                            before_exists=False,
                            reason="missing",
                        )
                    )
                elif current_value != value:
                    operations.append(
                        PlannedOperation(
                            kind="update",
                            path=path,
                            key=dotted_key,
                            value=value,
                            before_value=current_value,
                            before_exists=True,
                            reason="differs",
                        )
                    )
                continue

            # Flat key lookup (existing behavior)
            if key not in current:
                operations.append(
                    PlannedOperation(
                        kind="set",
                        path=path,
                        key=dotted_key,
                        value=value,
                        before_value=None,
                        before_exists=False,
                        reason="missing",
                    )
                )
                continue
            current_value = current[key]
            if isinstance(value, dict) and isinstance(current_value, dict):
                self._diff_mapping(current_value, value, [*prefix, key], path, operations)
            elif current_value != value:
                operations.append(
                    PlannedOperation(
                        kind="update",
                        path=path,
                        key=dotted_key,
                        value=value,
                        before_value=current_value,
                        before_exists=True,
                        reason="differs",
                    )
                )

    def _plan_deletions(
        self,
        root: Any,
        delete_keys: list[str],
        path: Path,
        operations: list[PlannedOperation],
    ) -> None:
        for dotted_key in delete_keys:
            current_value = mapping_value(root, dotted_key)
            if current_value is not _MISSING:
                operations.append(
                    PlannedOperation(
                        kind="delete",
                        path=path,
                        key=dotted_key,
                        before_value=current_value,
                        before_exists=True,
                        reason="requested",
                        before_key_parts=mapping_key_parts(root, dotted_key),
                    )
                )

    def _plan_line(self, document: Any, desired: DesiredState) -> PlanResult:
        if desired.managed_block_id is None:
            raise ValueError("line planning requires managed_block_id")

        block = document.block(desired.managed_block_id)
        operations: list[PlannedOperation] = []
        existing = None if block is None else [line.rstrip("\r\n") for line in block.lines]
        if existing != desired.lines:
            operations.append(
                PlannedOperation(
                    kind="replace_block",
                    path=desired.path,
                    key=desired.managed_block_id,
                    value=list(desired.lines),
                    before_value=existing,
                    before_exists=block is not None,
                    reason="block differs",
                )
            )
            return PlanResult(changed=True, operations=operations)

        return PlanResult(changed=False, operations=operations)
