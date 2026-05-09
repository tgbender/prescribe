import contextlib
import json
import sqlite3
from collections.abc import Callable, Iterator
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
CurrentLockGuard = Callable[[], None] | None
RecoveryBackupData = tuple[Path, bytes, int, int | None, str | None]


def _require_current(require_current: CurrentLockGuard) -> None:
    if require_current is not None:
        require_current()


@contextlib.contextmanager
def _state_write_connection(
    state_store: StateStore,
    connection: sqlite3.Connection | None,
) -> Iterator[sqlite3.Connection]:
    if connection is not None:
        yield connection
        return
    with state_store.transaction() as write_conn:
        yield write_conn


def perform_rollback(
    path: Path,
    state_store: StateStore,
    *,
    dry_run: bool = False,
    original: bool = False,
    resolver: ConflictResolver = None,
    connection: sqlite3.Connection | None = None,
    require_current: CurrentLockGuard = None,
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
        return perform_rollback_original(
            path,
            state_store,
            dry_run=dry_run,
            resolver=resolver,
            connection=connection,
            require_current=require_current,
        )

    batches = state_store.change_batches(path, connection=connection)
    if not batches:
        backups = [
            backup for backup in state_store.asset_backups(path, connection=connection) if backup.restored_at is None
        ]
        if not backups:
            backups = state_store.unrestored_asset_backups_for_target(path, connection=connection)
        if backups:
            return _restore_asset_backups(
                path,
                state_store,
                backups,
                dry_run=dry_run,
                connection=connection,
                require_current=require_current,
            )
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
        return _rollback_asset(
            path,
            state_store,
            batches,
            dry_run=dry_run,
            resolver=resolver,
            connection=connection,
            require_current=require_current,
        )
    if format_name == "asset-displaced":
        backups = [
            backup for backup in state_store.asset_backups(path, connection=connection) if backup.restored_at is None
        ]
        return _restore_asset_backups(
            path,
            state_store,
            backups,
            dry_run=dry_run,
            connection=connection,
            require_current=require_current,
        )

    checkpoint = state_store.latest_checkpoint(path, connection=connection)
    if not path.exists():
        if checkpoint is None or not checkpoint.original_exists:
            return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=dry_run)
        if dry_run:
            return OrchestrationResult(status="dry-run", applied=False, changed=True, dry_run=True)
        _require_current(require_current)
        ensure_safe_managed_write_path(path, operation="rollback write")
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
    _require_current(require_current)
    backup_data: RecoveryBackupData | None = None
    backup_operation = "rollback"
    wrote_document = False
    deleted_document = False
    written_text: str | None = None
    new_size: int | None = None
    new_mtime_ns: int | None = None
    new_hash: bytes | None = None
    if original_exists or _document_has_content(document):
        ensure_safe_managed_write_path(path, operation="rollback write")
        backup_data = _copy_recovery_backup(state_store, run_id=batches[-1].run_id, path=path)
        adapter.dump(document, path)
        written_text = decode_utf8_bytes(path.read_bytes(), path=path)
        new_stat = path.stat()
        new_size = new_stat.st_size
        new_mtime_ns = new_stat.st_mtime_ns
        new_hash = sha256_bytes(path.read_bytes())
        wrote_document = True
    elif path.exists():
        backup_operation = "rollback-delete"
        backup_data = _copy_recovery_backup(state_store, run_id=batches[-1].run_id, path=path)
        ensure_safe_unlink_path(path, operation="rollback delete")
        path.unlink()
        deleted_document = True

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
    with _state_write_connection(state_store, connection) as write_conn:
        if backup_data is not None:
            _record_recovery_backup_data(
                state_store,
                run_id=batches[-1].run_id,
                path=path,
                target_kind=format_name,
                operation=backup_operation,
                backup_data=backup_data,
                connection=write_conn,
            )
        if wrote_document:
            if written_text is None or new_hash is None or new_size is None or new_mtime_ns is None:
                raise RuntimeError(f"rollback did not capture new snapshot state for {path}")
            state_store.record_checkpoint(
                run_id=batches[-1].run_id,
                path=path,
                content_text=written_text,
                format=format_name,
                original_exists=original_exists,
                connection=write_conn,
            )
            state_store.record_snapshot(
                run_id=batches[-1].run_id,
                path=path,
                content_hash=new_hash,
                size=new_size,
                mtime_ns=new_mtime_ns,
                format=format_name,
                connection=write_conn,
            )
            if state_operations:
                state_store.record_change_batch(
                    run_id=batches[-1].run_id,
                    path=path,
                    operations=state_operations,
                    original_exists=original_exists,
                    format=format_name,
                    connection=write_conn,
                )
        elif deleted_document and state_operations:
            state_store.record_change_batch(
                run_id=batches[-1].run_id,
                path=path,
                operations=state_operations,
                original_exists=original_exists,
                format=format_name,
                connection=write_conn,
            )
        state_store.record_event(
            run_id=batches[-1].run_id,
            event_type="rollback",
            path=path,
            changed=True,
            summary="; ".join(parts),
            details=conflict_details,
            connection=write_conn,
        )
    return OrchestrationResult(status="rolled-back", applied=True, changed=True)


def perform_rollback_original(
    path: Path,
    state_store: StateStore,
    *,
    dry_run: bool = False,
    resolver: ConflictResolver = None,
    connection: sqlite3.Connection | None = None,
    require_current: CurrentLockGuard = None,
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
        _require_current(require_current)
        backup_data = _copy_recovery_backup(state_store, run_id=baseline.run_id, path=path)
        ensure_safe_unlink_path(path, operation="rollback-original delete")
        path.unlink()
        with _state_write_connection(state_store, connection) as write_conn:
            if backup_data is not None:
                _record_recovery_backup_data(
                    state_store,
                    run_id=baseline.run_id,
                    path=path,
                    target_kind=baseline.format or "file",
                    operation="rollback-original-delete",
                    backup_data=backup_data,
                    connection=write_conn,
                )
            _record_rollback_event(
                state_store,
                path,
                "rollback-original",
                "restored to pre-prescribe state (file deleted)",
                connection=write_conn,
            )
        return OrchestrationResult(status="rolled-back", applied=True, changed=True)

    return perform_rollback(
        path,
        state_store,
        dry_run=dry_run,
        resolver=resolver,
        connection=connection,
        require_current=require_current,
    )


def perform_restore(
    path: Path,
    state_store: StateStore,
    *,
    dry_run: bool = False,
    connection: sqlite3.Connection | None = None,
    require_current: CurrentLockGuard = None,
) -> OrchestrationResult:
    checkpoint = state_store.latest_checkpoint(path, connection=connection)
    if checkpoint is None:
        return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=dry_run)

    if dry_run:
        return OrchestrationResult(status="dry-run", applied=False, changed=True, dry_run=True)

    if not checkpoint.original_exists:
        if not path.exists():
            return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=dry_run)
        _require_current(require_current)
        ensure_safe_unlink_path(path, operation="restore delete")
        path.unlink()
        with _state_write_connection(state_store, connection) as write_conn:
            _record_rollback_event(
                state_store,
                path,
                "restore",
                "restored to last checkpoint (file deleted)",
                connection=write_conn,
            )
        return OrchestrationResult(status="restored", applied=True, changed=True)

    _require_current(require_current)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_safe_managed_write_path(path, operation="restore write")
    atomic_write_text(path, checkpoint.content_text, newline="")
    with _state_write_connection(state_store, connection) as write_conn:
        _record_rollback_event(
            state_store,
            path,
            "restore",
            "restored to last checkpoint",
            connection=write_conn,
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


def _copy_recovery_backup(
    state_store: StateStore,
    *,
    run_id: int,
    path: Path,
) -> RecoveryBackupData | None:
    if not path.exists() or path.is_symlink():
        return None
    return copy_to_recovery_backup(
        state_store=state_store,
        run_id=run_id,
        path=path,
    )


def _record_recovery_backup_data(
    state_store: StateStore,
    *,
    run_id: int,
    path: Path,
    target_kind: str,
    operation: str,
    backup_data: RecoveryBackupData,
    connection: sqlite3.Connection | None = None,
) -> None:
    backup_path, content_hash, size, mtime_ns, content_text = backup_data
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
    require_current: CurrentLockGuard = None,
) -> OrchestrationResult:
    changed = False
    skipped = False
    backup_records: list[tuple[int, Path, RecoveryBackupData]] = []
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
            before_permissions = _parse_permissions(operation.get("before_permissions"))
            if dry_run:
                return OrchestrationResult(status="dry-run", applied=False, changed=True, dry_run=True)
            if before_exists and before_is_symlink:
                return OrchestrationResult(
                    status="error",
                    applied=False,
                    changed=False,
                    error=f"asset rollback refused to recreate symlink path: {path}",
                )
            _require_current(require_current)
            backup_data: RecoveryBackupData | None = None
            if path.exists() or path.is_symlink():
                if path.exists() and not path.is_symlink():
                    backup_data = _copy_recovery_backup(state_store, run_id=batch.run_id, path=path)
                ensure_safe_unlink_path(path, operation="asset rollback delete")
                path.unlink()
            if before_exists:
                path.parent.mkdir(parents=True, exist_ok=True)
                ensure_safe_managed_write_path(path, operation="asset rollback restore")
                atomic_write_text(path, "" if before_value is None else str(before_value), newline="")
                if before_permissions is not None:
                    with contextlib.suppress(OSError):
                        path.chmod(before_permissions)
            if backup_data is not None:
                backup_records.append((batch.run_id, path, backup_data))
            changed = True

    if not changed:
        return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=dry_run)

    with _state_write_connection(state_store, connection) as write_conn:
        for run_id, backup_path, backup_data in backup_records:
            _record_recovery_backup_data(
                state_store,
                run_id=run_id,
                path=backup_path,
                target_kind="asset",
                operation="asset-rollback",
                backup_data=backup_data,
                connection=write_conn,
            )
        state_store.record_event(
            run_id=batches[-1].run_id,
            event_type="rollback",
            path=path,
            changed=True,
            summary="rolled back asset" + ("; skipped externally modified version" if skipped else ""),
            connection=write_conn,
        )
    return OrchestrationResult(status="rolled-back", applied=True, changed=True)


def _restore_asset_backups(
    path: Path,
    state_store: StateStore,
    backups: list[Any],
    *,
    dry_run: bool,
    connection: sqlite3.Connection | None,
    require_current: CurrentLockGuard = None,
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
    _require_current(require_current)
    try:
        for backup in reversed(backups):
            ensure_safe_managed_write_path(backup.original_path, operation="asset backup restore")
            restore_backup(backup_path=backup.backup_path, original_path=backup.original_path)
            with _state_write_connection(state_store, connection) as write_conn:
                state_store.mark_asset_backup_restored(backup.id, connection=write_conn)
    except (OSError, UnsafePathError) as exc:
        return OrchestrationResult(status="error", applied=False, changed=False, error=f"{backup.backup_path}: {exc}")
    with _state_write_connection(state_store, connection) as write_conn:
        state_store.record_event(
            run_id=backups[-1].run_id,
            event_type="restore",
            path=path,
            changed=True,
            summary=f"restored {len(backups)} displaced asset backup(s)",
            connection=write_conn,
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
