import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from prescribe._util import (
    _MISSING,
    delete_mapping_value,
    mapping_value,
    set_mapping_value,
    sha256_bytes,
)
from prescribe.adapters import adapter_for_path
from prescribe.core.result import OrchestrationResult
from prescribe.document import Document
from prescribe.state import StateStore

ConflictResolver = Callable[[str], bool] | None


def perform_rollback(
    path: Path,
    state_store: StateStore,
    *,
    dry_run: bool = False,
    original: bool = False,
    resolver: ConflictResolver = None,
    connection: sqlite3.Connection | None = None,
) -> OrchestrationResult:
    if original:
        return perform_rollback_original(path, state_store, dry_run=dry_run, resolver=resolver, connection=connection)

    batches = state_store.change_batches(path, connection=connection)
    if not batches:
        return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=dry_run)

    format_name = batches[-1].format
    if format_name is None:
        return OrchestrationResult(
            status="error",
            applied=False,
            changed=False,
            error=f"missing format history for {path}",
        )

    checkpoint = state_store.latest_checkpoint(path, connection=connection)
    if not path.exists():
        if checkpoint is None or not checkpoint.original_exists:
            return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=dry_run)
        if dry_run:
            return OrchestrationResult(status="dry-run", applied=False, changed=True, dry_run=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(checkpoint.content_text, encoding="utf-8")

    adapter = adapter_for_path(path, fmt=format_name)
    try:
        document = adapter.load(path)
        changed = False
        all_skipped: list[str] = []
        all_force_reverted: list[str] = []
        for batch in reversed(batches):
            batch_changed, batch_skipped, batch_forced = rollback_batch(document, batch.operations, resolver)
            changed = batch_changed or changed
            all_skipped.extend(batch_skipped)
            all_force_reverted.extend(batch_forced)
    except Exception as exc:
        return OrchestrationResult(status="error", applied=False, changed=False, error=str(exc))

    if not changed:
        return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=dry_run)

    if dry_run:
        return OrchestrationResult(status="dry-run", applied=False, changed=True, dry_run=True)

    original_exists = batches[0].original_exists
    if original_exists or _document_has_content(document):
        adapter.dump(document, path)
        written_text = path.read_text(encoding="utf-8")
        new_stat = path.stat()
        new_hash = sha256_bytes(path.read_bytes())
        state_store.record_checkpoint(
            run_id=batches[-1].run_id,
            path=path,
            content_text=written_text,
            format=format_name,
            original_exists=original_exists,
            connection=connection,
        )
        state_store.record_snapshot(
            run_id=batches[-1].run_id,
            path=path,
            content_hash=new_hash,
            size=new_stat.st_size,
            mtime_ns=new_stat.st_mtime_ns,
            format=format_name,
            connection=connection,
        )
    elif path.exists():
        path.unlink()

    parts = [f"rolled back {len(batches)} change batch(es)"]
    if all_force_reverted:
        parts.append(f"{len(all_force_reverted)} key(s) force-reverted")
    if all_skipped:
        parts.append(f"{len(all_skipped)} key(s) skipped (externally modified)")
    conflict_details = (
        json.dumps({"skipped": all_skipped, "force_reverted": all_force_reverted})
        if all_skipped or all_force_reverted
        else None
    )
    state_store.record_event(
        run_id=batches[-1].run_id,
        event_type="rollback",
        path=path,
        changed=True,
        summary="; ".join(parts),
        details=conflict_details,
        connection=connection,
    )
    return OrchestrationResult(status="rolled-back", applied=True, changed=True)


def perform_rollback_original(
    path: Path,
    state_store: StateStore,
    *,
    dry_run: bool = False,
    resolver: ConflictResolver = None,
    connection: sqlite3.Connection | None = None,
) -> OrchestrationResult:
    baseline = state_store.original_baseline(path, connection=connection)
    if baseline is None:
        return OrchestrationResult(
            status="noop",
            applied=False,
            changed=False,
            dry_run=dry_run,
            error="no baseline recorded for this path",
        )

    if not baseline.original_exists:
        if not path.exists():
            return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=dry_run)
        if dry_run:
            return OrchestrationResult(status="dry-run", applied=False, changed=True, dry_run=True)
        path.unlink()
        _record_rollback_event(
            state_store,
            path,
            "rollback-original",
            "restored to pre-prescribe state (file deleted)",
            connection=connection,
        )
        return OrchestrationResult(status="rolled-back", applied=True, changed=True)

    return perform_rollback(path, state_store, dry_run=dry_run, resolver=resolver, connection=connection)


def perform_restore(
    path: Path,
    state_store: StateStore,
    *,
    dry_run: bool = False,
    connection: sqlite3.Connection | None = None,
) -> OrchestrationResult:
    checkpoint = state_store.latest_checkpoint(path, connection=connection)
    if checkpoint is None:
        return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=dry_run)

    if dry_run:
        return OrchestrationResult(status="dry-run", applied=False, changed=True, dry_run=True)

    if not checkpoint.original_exists:
        if not path.exists():
            return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=dry_run)
        path.unlink()
        _record_rollback_event(
            state_store,
            path,
            "restore",
            "restored to last checkpoint (file deleted)",
            connection=connection,
        )
        return OrchestrationResult(status="restored", applied=True, changed=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(checkpoint.content_text, encoding="utf-8")
    _record_rollback_event(
        state_store,
        path,
        "restore",
        "restored to last checkpoint",
        connection=connection,
    )
    return OrchestrationResult(status="restored", applied=True, changed=True)


def _record_rollback_event(
    state_store: StateStore,
    path: Path,
    event_type: str,
    summary: str,
    *,
    connection: sqlite3.Connection | None = None,
) -> None:
    run_id = state_store.latest_run_id(connection=connection)
    if run_id is None:
        raise RuntimeError(f"cannot record rollback event for {path}: no runs in state store")
    state_store.record_event(
        run_id=run_id,
        event_type=event_type,
        path=path,
        changed=True,
        summary=summary,
        connection=connection,
    )


def rollback_batch(
    document: Document, operations: list[dict[str, Any]], resolver: ConflictResolver = None
) -> tuple[bool, list[str], list[str]]:
    format_name = getattr(document, "format", None)
    if format_name in {"toml", "yaml", "jsonc"}:
        return _rollback_mapping(document, operations, resolver)
    if format_name == "line":
        return _rollback_line(document, operations, resolver)
    raise ValueError(f"unsupported document format for rollback: {format_name}")


def _rollback_mapping(
    document: Document, operations: list[dict[str, Any]], resolver: ConflictResolver = None
) -> tuple[bool, list[str], list[str]]:
    changed = False
    skipped: list[str] = []
    force_reverted: list[str] = []
    for operation in reversed(operations):
        kind = operation["kind"]
        key = operation.get("key")
        if key is None:
            raise ValueError("rollback operation missing key")
        current_value = mapping_value(document.root, key)
        if kind == "set":
            if current_value == operation.get("value"):
                delete_mapping_value(document.root, key)
                changed = True
            elif resolver is not None and resolver(key):
                delete_mapping_value(document.root, key)
                changed = True
                force_reverted.append(key)
            else:
                skipped.append(key)
            continue
        if kind == "update":
            if current_value == operation.get("value"):
                set_mapping_value(document.root, key, operation.get("before_value"))
                changed = True
            elif resolver is not None and resolver(key):
                set_mapping_value(document.root, key, operation.get("before_value"))
                changed = True
                force_reverted.append(key)
            else:
                skipped.append(key)
            continue
        if kind == "delete":
            if current_value is _MISSING:
                set_mapping_value(document.root, key, operation.get("before_value"))
                changed = True
            continue
        raise ValueError(f"unsupported rollback mapping operation: {kind}")
    return changed, skipped, force_reverted


def _rollback_line(
    document: Document, operations: list[dict[str, Any]], resolver: ConflictResolver = None
) -> tuple[bool, list[str], list[str]]:
    changed = False
    skipped: list[str] = []
    force_reverted: list[str] = []
    for operation in reversed(operations):
        kind = operation["kind"]
        key = operation.get("key")
        if key is None:
            raise ValueError("rollback operation missing key")
        block_id = str(key)
        block = document.root.block(block_id)
        current_lines = None if block is None else [line.rstrip("\r\n") for line in block.lines]
        if kind == "replace_block":
            if current_lines == operation.get("value"):
                before_value = operation.get("before_value")
                if before_value is None:
                    document.root.remove_block(block_id)
                else:
                    document.root.ensure_block(block_id, list(before_value))
                changed = True
            elif resolver is not None and resolver(block_id):
                before_value = operation.get("before_value")
                if before_value is None:
                    document.root.remove_block(block_id)
                else:
                    document.root.ensure_block(block_id, list(before_value))
                changed = True
                force_reverted.append(block_id)
            else:
                skipped.append(block_id)
            continue
        if kind == "delete_block":
            if block is None:
                before_value = operation.get("before_value")
                if before_value is not None:
                    document.root.ensure_block(block_id, list(before_value))
                changed = True
            continue
        raise ValueError(f"unsupported rollback line operation: {kind}")
    return changed, skipped, force_reverted


def _document_has_content(document: Document) -> bool:
    if getattr(document, "format", None) == "line":
        return bool(document.root.render())
    return bool(document.root)
