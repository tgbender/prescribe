"""Diff generation for prescribe status --diff.

Produces unified diffs showing what would change before applying.
"""

from __future__ import annotations

import difflib
import tempfile
from pathlib import Path

from prescribe.core.planner import PlannedOperation
from prescribe.document import Adapter, Document


def diff_file(
    *,
    document: Document,
    plan: list[PlannedOperation],
    adapter: Adapter,
    path: Path,
) -> str | None:
    """Return a unified diff string if the file would change, or None if in sync.

    Applies the planned operations to a copy of the document, dumps both
    (current and desired) to temp files, and diffs the serialized output.
    """
    if not any(op.kind in {"set", "update", "delete", "replace_block"} for op in plan):
        return None

    current_text = _serialize(document, adapter)

    desired_doc = _apply_plan_to_copy(document, plan)
    desired_text = _serialize(desired_doc, adapter)

    if current_text == desired_text:
        return None

    diff = difflib.unified_diff(
        current_text.splitlines(keepends=True),
        desired_text.splitlines(keepends=True),
        fromfile=str(path),
        tofile=str(path) + " (desired)",
    )
    return "".join(diff)


def diff_new_file(*, plan: list[PlannedOperation], adapter: Adapter, path: Path) -> str | None:
    """Return a diff showing what a new file would contain, or None if no changes."""
    if not any(op.kind in {"set", "update", "delete", "replace_block"} for op in plan):
        return None

    empty_doc = _empty_document_for(path, adapter)
    desired_doc = _apply_plan_to_copy(empty_doc, plan)
    desired_text = _serialize(desired_doc, adapter)

    if not desired_text.strip():
        return None

    diff = difflib.unified_diff(
        [],
        desired_text.splitlines(keepends=True),
        fromfile=str(path),
        tofile=str(path) + " (new)",
    )
    return "".join(diff)


# ── helpers ───────────────────────────────────────────────


def _serialize(document: Document, adapter: Adapter) -> str:
    """Serialize a document to text via its adapter, using a temp directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "config"
        adapter.dump(document, tmp_path)
        return tmp_path.read_text()


def _apply_plan_to_copy(document: Document, plan: list[PlannedOperation]) -> Document:
    """Return a deep copy of the document with the planned operations applied."""
    import copy

    from prescribe.core.apply import apply_operations

    copy_doc = Document(
        path=document.path,
        format=getattr(document, "format", None) or "toml",
        root=copy.deepcopy(document.root),
    )
    apply_operations(copy_doc, plan)
    return copy_doc


def _empty_document_for(path: Path, adapter: Adapter) -> Document:
    """Create an empty document suitable for the format."""
    fmt = getattr(adapter, "format", None) or "toml"
    if fmt in {"toml", "yaml", "jsonc"}:
        return Document(path=path, format=fmt, root={})
    if fmt == "line":
        from prescribe.adapters.line import LineDocument, preferred_newline

        return Document(
            path=path,
            format="line",
            root=LineDocument(path=path, newline=preferred_newline(path)),
        )
    raise ValueError(f"unsupported format: {fmt}")
