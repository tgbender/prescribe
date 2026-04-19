from __future__ import annotations

import hashlib
from typing import Any

_MISSING = object()


def sha256_bytes(content: bytes) -> bytes:
    return hashlib.sha256(content).digest()


def mapping_value(root: Any, dotted_key: str) -> Any:
    node = root
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    if not isinstance(node, dict) or parts[-1] not in node:
        return _MISSING
    return node[parts[-1]]


def resolve_parent(root: Any, dotted_key: str) -> tuple[Any, str]:
    parts = dotted_key.split(".")
    node = root
    for part in parts[:-1]:
        if part not in node:
            node[part] = {}
        node = node[part]
    return node, parts[-1]


def set_mapping_value(root: Any, dotted_key: str, value: Any) -> None:
    parent, leaf = resolve_parent(root, dotted_key)
    parent[leaf] = value


def delete_mapping_value(root: Any, dotted_key: str) -> None:
    parent, leaf = resolve_parent(root, dotted_key)
    del parent[leaf]
