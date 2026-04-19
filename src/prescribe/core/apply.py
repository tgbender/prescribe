from __future__ import annotations

from typing import Any

from prescribe._util import resolve_parent
from prescribe.core.planner import PlannedOperation


def apply_operations(document: Any, operations: list[PlannedOperation]) -> Any:
    format_name = getattr(document, "format", None)
    if format_name in {"toml", "yaml", "json5", "jsonc"}:
        _apply_mapping(document.root, operations)
        return document
    if format_name == "line":
        _apply_line(document.root, operations)
        return document
    raise ValueError(f"unsupported document format: {format_name}")


def _apply_mapping(root: Any, operations: list[PlannedOperation]) -> None:
    for operation in operations:
        if operation.key is None:
            raise ValueError(f"mapping operation missing key: {operation.kind}")
        parent, leaf = resolve_parent(root, operation.key)
        if operation.kind in {"set", "update"}:
            parent[leaf] = operation.value
            continue
        if operation.kind == "delete":
            del parent[leaf]
            continue
        raise ValueError(f"unsupported mapping operation: {operation.kind}")


def _apply_line(document: Any, operations: list[PlannedOperation]) -> None:
    for operation in operations:
        if operation.kind == "replace_block":
            document.ensure_block(str(operation.key), list(operation.value or []))
            continue
        if operation.kind == "delete_block":
            document.remove_block(str(operation.key))
            continue
        raise ValueError(f"unsupported line operation: {operation.kind}")
