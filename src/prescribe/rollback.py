import contextlib
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
from prescribe.atomic import atomic_write_text
from prescribe.backups import copy_to_recovery_backup, restore_backup
from prescribe.core.result import OrchestrationResult
from prescribe.document import Document
from prescribe.encoding import decode_utf8_bytes
from prescribe.fs_safety import UnsafePathError, ensure_safe_managed_write_path, ensure_safe_unlink_path
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
    if path.is_symlink():
        return OrchestrationResult(
            status="error",
            applied=False,
            changed=False,
            error=f"rollback refused for symlink path: {path}",
            dry_run=dry_run,
        )

    if original:
        return perform_rollback_original(path, state_store, dry_run=dry_run, resolver=resolver, connection=connection)

    batches = state_store.change_batches(path, connection=connection)
    if not batches:
        backups = [
            backup for backup in state_store.asset_backups(path, connection=connection) if backup.restored_at is None
        ]
        if not backups:
            backups = state_store.unrestored_asset_backups_for_target(path, connection=connection)
        if backups:
            return _restore_asset_backups(path, state_store, backups, dry_run=dry_run, connection=connection)
        return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=dry_run)

    format_name = batches[-1].format
    if format_name is None:
        return OrchestrationResult(
            status="error",
            applied=False,
            changed=False,
            error=f"missing format history for {path}",
        )

    if format_name == "asset":
        return _rollback_asset(path, state_store, batches, dry_run=dry_run, resolver=resolver, connection=connection)
    if format_name == "asset-displaced":
        backups = [
            backup for backup in state_store.asset_backups(path, connection=connection) if backup.restored_at is None
        ]
        return _restore_asset_backups(path, state_store, backups, dry_run=dry_run, connection=connection)

    checkpoint = state_store.latest_checkpoint(path, connection=connection)
    if not path.exists():
        if checkpoint is None or not checkpoint.original_exists:
            return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=dry_run)
        if dry_run:
            return OrchestrationResult(status="dry-run", applied=False, changed=True, dry_run=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, checkpoint.content_text, newline="")

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
    state_operations = _current_state_operations(
        document,
        batches,
        skipped_keys=set(all_skipped),
    )
    if original_exists or _document_has_content(document):
        _record_recovery_backup(
            state_store,
            run_id=batches[-1].run_id,
            path=path,
            target_kind=format_name,
            operation="rollback",
            connection=connection,
        )
        adapter.dump(document, path)
        written_text = decode_utf8_bytes(path.read_bytes(), path=path)
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
        if state_operations:
            state_store.record_change_batch(
                run_id=batches[-1].run_id,
                path=path,
                operations=state_operations,
                original_exists=original_exists,
                format=format_name,
                connection=connection,
            )
    elif path.exists():
        _record_recovery_backup(
            state_store,
            run_id=batches[-1].run_id,
            path=path,
            target_kind=format_name,
            operation="rollback-delete",
            connection=connection,
        )
        ensure_safe_unlink_path(path, operation="rollback delete")
        path.unlink()
        if state_operations:
            state_store.record_change_batch(
                run_id=batches[-1].run_id,
                path=path,
                operations=state_operations,
                original_exists=original_exists,
                format=format_name,
                connection=connection,
            )

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
        _record_recovery_backup(
            state_store,
            run_id=baseline.run_id,
            path=path,
            target_kind=baseline.format or "file",
            operation="rollback-original-delete",
            connection=connection,
        )
        ensure_safe_unlink_path(path, operation="rollback-original delete")
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
        ensure_safe_unlink_path(path, operation="restore delete")
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
    ensure_safe_managed_write_path(path, operation="restore write")
    atomic_write_text(path, checkpoint.content_text, newline="")
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


def _record_recovery_backup(
    state_store: StateStore,
    *,
    run_id: int,
    path: Path,
    target_kind: str,
    operation: str,
    connection: sqlite3.Connection | None = None,
) -> None:
    if not path.exists() or path.is_symlink():
        return
    backup_path, content_hash, size, mtime_ns, content_text = copy_to_recovery_backup(
        state_store=state_store,
        run_id=run_id,
        path=path,
    )
    state_store.record_recovery_backup(
        run_id=run_id,
        target_path=path,
        target_kind=target_kind,
        operation=operation,
        backup_path=backup_path,
        content_hash=content_hash,
        size=size,
        mtime_ns=mtime_ns,
        content_text=content_text,
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


def _rollback_asset(
    path: Path,
    state_store: StateStore,
    batches: list[Any],
    *,
    dry_run: bool,
    resolver: ConflictResolver,
    connection: sqlite3.Connection | None,
) -> OrchestrationResult:
    changed = False
    skipped = False
    for batch in reversed(batches):
        for operation in reversed(batch.operations):
            if operation.get("kind") != "replace_file":
                return OrchestrationResult(
                    status="error",
                    applied=False,
                    changed=False,
                    error=f"unsupported rollback asset operation: {operation.get('kind')}",
                )
            current_text = decode_utf8_bytes(path.read_bytes(), path=path) if path.exists() else None
            expected = operation.get("value")
            if current_text != expected and not (resolver is not None and resolver("file")):
                skipped = True
                continue
            before_exists = bool(operation.get("before_exists"))
            before_value = operation.get("before_value")
            before_is_symlink = bool(operation.get("before_is_symlink"))
            before_symlink_target = operation.get("before_symlink_target")
            before_permissions = _parse_permissions(operation.get("before_permissions"))
            if dry_run:
                return OrchestrationResult(status="dry-run", applied=False, changed=True, dry_run=True)
            if path.exists() or path.is_symlink():
                if path.exists() and not path.is_symlink():
                    _record_recovery_backup(
                        state_store,
                        run_id=batch.run_id,
                        path=path,
                        target_kind="asset",
                        operation="asset-rollback",
                        connection=connection,
                    )
                ensure_safe_unlink_path(path, operation="asset rollback delete")
                path.unlink()
            if before_exists:
                path.parent.mkdir(parents=True, exist_ok=True)
                if before_is_symlink and before_symlink_target is not None:
                    return OrchestrationResult(
                        status="error",
                        applied=False,
                        changed=False,
                        error=f"asset rollback refused to recreate symlink path: {path}",
                    )
                ensure_safe_managed_write_path(path, operation="asset rollback restore")
                atomic_write_text(path, "" if before_value is None else str(before_value), newline="")
                if before_permissions is not None:
                    with contextlib.suppress(OSError):
                        path.chmod(before_permissions)
            changed = True

    if not changed:
        return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=dry_run)

    state_store.record_event(
        run_id=batches[-1].run_id,
        event_type="rollback",
        path=path,
        changed=True,
        summary="rolled back asset" + ("; skipped externally modified version" if skipped else ""),
        connection=connection,
    )
    return OrchestrationResult(status="rolled-back", applied=True, changed=True)


def _restore_asset_backups(
    path: Path,
    state_store: StateStore,
    backups: list[Any],
    *,
    dry_run: bool,
    connection: sqlite3.Connection | None,
) -> OrchestrationResult:
    if not backups:
        return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=dry_run)
    for backup in backups:
        if backup.original_path.exists() or backup.original_path.is_symlink():
            return OrchestrationResult(
                status="conflict",
                applied=False,
                changed=True,
                error=f"cannot restore displaced asset because path exists: {backup.original_path}",
                dry_run=dry_run,
            )
    if dry_run:
        return OrchestrationResult(status="dry-run", applied=False, changed=True, dry_run=True)
    try:
        for backup in reversed(backups):
            ensure_safe_managed_write_path(backup.original_path, operation="asset backup restore")
            restore_backup(backup_path=backup.backup_path, original_path=backup.original_path)
            state_store.mark_asset_backup_restored(backup.id, connection=connection)
    except UnsafePathError as exc:
        return OrchestrationResult(status="error", applied=False, changed=False, error=str(exc))
    state_store.record_event(
        run_id=backups[-1].run_id,
        event_type="restore",
        path=path,
        changed=True,
        summary=f"restored {len(backups)} displaced asset backup(s)",
        connection=connection,
    )
    return OrchestrationResult(status="restored", applied=True, changed=True)


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
            key_parts = operation.get("before_key_parts")
            if key_parts:
                parent = document.root
                for part in key_parts[:-1]:
                    if not isinstance(parent, dict):
                        break
                    parent = parent.setdefault(part, {})
                if isinstance(parent, dict) and key_parts[-1] not in parent:
                    parent[key_parts[-1]] = operation.get("before_value")
                    changed = True
            elif current_value is _MISSING:
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


def _current_state_operations(
    document: Document,
    batches: list[Any],
    *,
    skipped_keys: set[str],
) -> list[dict[str, Any]]:
    keys = _changed_operation_keys(batches) - skipped_keys
    if not keys:
        return []

    if getattr(document, "format", None) == "line":
        operations: list[dict[str, Any]] = []
        for key in sorted(keys):
            block = document.root.block(key)
            if block is None:
                operations.append({"kind": "delete_block", "key": key, "value": None})
                continue
            operations.append(
                {
                    "kind": "replace_block",
                    "key": key,
                    "value": [line.rstrip("\r\n") for line in block.lines],
                }
            )
        return operations

    operations = []
    for key in sorted(keys):
        value = mapping_value(document.root, key)
        if value is _MISSING:
            operations.append({"kind": "delete", "key": key, "value": None})
            continue
        operations.append({"kind": "update", "key": key, "value": value})
    return operations


def _changed_operation_keys(batches: list[Any]) -> set[str]:
    keys: set[str] = set()
    for batch in batches:
        for operation in batch.operations:
            key = operation.get("key")
            if key is not None:
                keys.add(str(key))
    return keys


def _parse_permissions(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        return int(value, 8)
    if isinstance(value, int):
        return value
    return None
