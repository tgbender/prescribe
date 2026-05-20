import hashlib
from collections.abc import Iterable
from typing import Any

_MISSING = object()


def sha256_bytes(content: bytes) -> bytes:
    return hashlib.sha256(content).digest()


def mapping_value(root: Any, dotted_key: str) -> Any:
    """Look up a dotted key, resolving nested paths with flat-key fallback.

    Recursively resolves at each nesting level: full-key-as-flat first,
    then flat-key prefix + recursive remainder, then standard dot nesting.
    """
    if not isinstance(root, dict):
        return _MISSING

    # 1) Try the full key as a flat key.
    if dotted_key in root:
        return root[dotted_key]

    # 2) Try progressively shorter flat-key prefixes, recursively.
    parts = dotted_key.split(".")
    for split_at in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:split_at])
        if prefix in root and isinstance(root[prefix], dict):
            rest = ".".join(parts[split_at:])
            result = mapping_value(root[prefix], rest)
            if result is not _MISSING:
                return result

    # 3) Standard dot-separated nesting.
    node = root
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    if not isinstance(node, dict) or parts[-1] not in node:
        return _MISSING
    return node[parts[-1]]


def resolve_parent(root: Any, dotted_key: str) -> tuple[Any, str]:
    """Resolve to (parent_dict, leaf_key), auto-creating intermediate dicts.

    Mirrors :func:`mapping_value` lookup order.  Creates missing dicts only
    for the path actually taken (preserving existing flat-key structures).
    """
    if not isinstance(root, dict):
        raise KeyError(dotted_key)

    # 1) Full key exists as a flat key → operate on root directly.
    if dotted_key in root:
        return root, dotted_key

    # 2) Try flat-key prefixes recursively.
    parts = dotted_key.split(".")
    for split_at in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:split_at])
        if prefix in root and isinstance(root[prefix], dict):
            rest = ".".join(parts[split_at:])
            try:
                return resolve_parent(root[prefix], rest)
            except KeyError:
                continue

    # 3) Standard dot nesting (creates missing intermediates).
    node = root
    for part in parts[:-1]:
        if part not in node:
            node[part] = {}
        node = node[part]
    return node, parts[-1]


def set_mapping_value(root: Any, dotted_key: str, value: Any) -> None:
    parent, leaf = resolve_parent(root, dotted_key)
    parent[leaf] = value


def delete_mapping_value(root: Any, dotted_key: str, *, prune_empty_parents: Iterable[str] = ()) -> None:
    if mapping_value(root, dotted_key) is _MISSING:
        return
    parent, leaf = resolve_parent(root, dotted_key)
    if leaf in parent:
        del parent[leaf]
    for parent_key in sorted(prune_empty_parents, key=lambda key: key.count("."), reverse=True):
        _delete_if_empty_dict(root, parent_key)


def _delete_if_empty_dict(root: Any, dotted_key: str) -> None:
    if mapping_value(root, dotted_key) != {}:
        return
    parent, leaf = resolve_parent(root, dotted_key)
    if parent.get(leaf) == {}:
        del parent[leaf]
