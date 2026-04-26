import os
import socket
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prescribe.spec import Spec

from prescribe._util import _MISSING, mapping_value, sha256_bytes
from prescribe.adapters import adapter_for_path
from prescribe.core import DesiredState, Planner, detect_conflict
from prescribe.core.apply import apply_operations
from prescribe.core.conflict import (
    ConflictResult,
    FileFingerprint,
    file_fingerprint,
)
from prescribe.core.planner import PlannedOperation
from prescribe.core.result import OrchestrationResult
from prescribe.document import Adapter, Document
from prescribe.rollback import perform_rollback
from prescribe.spec import SpecTarget
from prescribe.state import StateStore
from prescribe.state.sqlite import ChangeBatchRecord

PLATFORM_MATCHERS: dict[str, Callable[[], bool]] = {
    "linux": lambda: sys.platform.startswith("linux"),
    "macos": lambda: sys.platform == "darwin",
    "windows": lambda: sys.platform == "win32",
}


def current_machine() -> str:
    override = os.environ.get("PRESCRIBE_MACHINE")
    if override:
        return override
    return socket.gethostname().split(".")[0]


def platform_matches(selectors: list[str]) -> bool:
    if not selectors:
        return True
    return any(PLATFORM_MATCHERS.get(selector, lambda: False)() for selector in selectors)


def machine_matches(selectors: list[str]) -> bool:
    if not selectors:
        return True
    machine = current_machine()
    return any(selector == machine or selector == "all" for selector in selectors)


class Orchestrator:
    def __init__(self, state_store: StateStore) -> None:
        self.state_store = state_store
        self.planner = Planner()

    def run(
        self,
        spec_path: Path | str,
        *,
        tool_version: str | None = None,
        dry_run: bool = False,
    ) -> list[OrchestrationResult]:
        from prescribe.spec import SpecLoader

        spec_path = Path(spec_path)
        spec = SpecLoader().load(spec_path)
        spec_hash = sha256_bytes(spec_path.read_bytes())
        if dry_run:
            self.state_store.initialize()
            return self._run_targets(None, spec_hash, spec, dry_run=True)

        self.state_store.initialize()
        with self.state_store.transaction() as connection:
            run = self.state_store.start_run(
                spec_hash=spec_hash,
                tool_version=tool_version,
                platform=sys.platform,
                host=current_machine(),
                connection=connection,
            )
            return self._run_targets(run.id, spec_hash, spec, dry_run=False, connection=connection)

    def _run_targets(
        self,
        run_id: int | None,
        spec_hash: bytes,
        spec: "Spec",
        *,
        dry_run: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> list[OrchestrationResult]:
        from prescribe.spec import Spec

        assert isinstance(spec, Spec)
        results: list[OrchestrationResult] = []
        for target in spec.targets:
            results.append(self._process_target(run_id, spec_hash, target, dry_run=dry_run, connection=connection))
        return results

    def rollback(
        self,
        target_path: Path | str,
        *,
        dry_run: bool = False,
    ) -> OrchestrationResult:
        path = Path(target_path)
        if not dry_run:
            self.state_store.initialize()
        elif not self.state_store.path.exists():
            return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=True)

        if not dry_run:
            with self.state_store.transaction() as connection:
                return perform_rollback(path, self.state_store, dry_run=False, connection=connection)
        return perform_rollback(path, self.state_store, dry_run=True)

    def _process_target(
        self,
        run_id: int | None,
        spec_hash: bytes,
        target: SpecTarget,
        *,
        dry_run: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> OrchestrationResult:
        if not platform_matches(target.platforms):
            return OrchestrationResult(status="skipped", applied=False, changed=False, skipped=True)
        if not machine_matches(target.machine):
            return OrchestrationResult(status="skipped", applied=False, changed=False, skipped=True)

        adapter = adapter_for_path(target.path, fmt=target.format)

        try:
            if not target.path.exists():
                return self._process_new_file(
                    run_id,
                    spec_hash,
                    target,
                    adapter,
                    dry_run=dry_run,
                    connection=connection,
                )

            return self._process_existing_file(
                run_id,
                spec_hash,
                target,
                adapter,
                dry_run=dry_run,
                connection=connection,
            )
        except Exception as exc:
            if run_id is not None:
                self.state_store.record_event(
                    run_id=run_id,
                    event_type="error",
                    path=target.path,
                    changed=False,
                    summary=str(exc),
                    details=f"{exc.__class__.__name__}: {exc}",
                    connection=connection,
                )
            return OrchestrationResult(status="error", applied=False, changed=False, error=str(exc))

    def _process_new_file(
        self,
        run_id: int | None,
        spec_hash: bytes,
        target: SpecTarget,
        adapter: Adapter,
        *,
        dry_run: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> OrchestrationResult:
        document = _empty_document(target.path, target.format)
        plan = self.planner.plan(document, target_to_desired(target))
        if not plan.changed:
            return OrchestrationResult(
                status="dry-run" if dry_run else "noop",
                applied=False,
                changed=False,
                dry_run=dry_run,
            )

        if dry_run:
            return OrchestrationResult(status="dry-run", applied=False, changed=True, dry_run=True)

        if target.path.exists():
            assert run_id is not None
            conflict = ConflictResult(
                path=target.path,
                changed=True,
                reason="file appeared during orchestration",
                baseline_fingerprint=None,
                current_fingerprint=_current_fingerprint(target.path),
            )
            self._record_conflict(run_id, conflict, connection=connection)
            return OrchestrationResult(status="conflict", applied=False, changed=True, conflict=conflict)

        original_exists = target.path.exists()
        return self._apply_and_record(
            run_id=run_id,
            spec_hash=spec_hash,
            target=target,
            document=document,
            operations=plan.operations,
            original_exists=original_exists,
            adapter=adapter,
            event_type="created",
            connection=connection,
        )

    def _process_existing_file(
        self,
        run_id: int | None,
        spec_hash: bytes,
        target: SpecTarget,
        adapter: Adapter,
        *,
        dry_run: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> OrchestrationResult:
        before = _current_fingerprint(target.path)
        document = adapter.load(target.path)
        plan = self.planner.plan(document, target_to_desired(target))
        managed_conflict: ConflictResult | None = None
        if run_id is not None or dry_run:
            if connection is None and run_id is None:
                with self.state_store.connect() as read_connection:
                    managed_state = _managed_state_from_batches(
                        self.state_store.change_batches(target.path, connection=read_connection)
                    )
                    snapshot = self.state_store.latest_snapshot(target.path, connection=read_connection)
                    baseline = (
                        file_fingerprint(
                            snapshot.path,
                            snapshot.hash_algo,
                            snapshot.content_hash,
                            snapshot.size,
                            snapshot.mtime_ns,
                        )
                        if snapshot is not None
                        else None
                    )
                    managed_conflict = _detect_managed_conflict(
                        document,
                        plan.operations,
                        managed_state,
                        baseline_fingerprint=baseline,
                        current_fingerprint=before,
                    )
            else:
                managed_state = _managed_state_from_batches(
                    self.state_store.change_batches(target.path, connection=connection)
                )
                snapshot = self.state_store.latest_snapshot(target.path, connection=connection)
                baseline = (
                    file_fingerprint(
                        snapshot.path,
                        snapshot.hash_algo,
                        snapshot.content_hash,
                        snapshot.size,
                        snapshot.mtime_ns,
                    )
                    if snapshot is not None
                    else None
                )
                managed_conflict = _detect_managed_conflict(
                    document,
                    plan.operations,
                    managed_state,
                    baseline_fingerprint=baseline,
                    current_fingerprint=before,
                )

            if managed_conflict is not None:
                if run_id is not None and not dry_run:
                    self._record_conflict(run_id, managed_conflict, connection=connection)
                return OrchestrationResult(
                    status="conflict",
                    applied=False,
                    changed=True,
                    conflict=managed_conflict,
                    dry_run=dry_run,
                )

        if not plan.changed:
            if run_id is not None and not dry_run:
                self.state_store.record_checked(
                    run_id=run_id,
                    path=target.path,
                    content_hash=before.content_hash,
                    size=before.size,
                    mtime_ns=before.mtime_ns,
                    format=target.format,
                    spec_hash=spec_hash,
                    summary="no changes",
                    connection=connection,
                )
            return OrchestrationResult(
                status="dry-run" if dry_run else "noop",
                applied=False,
                changed=False,
                dry_run=dry_run,
            )

        after = _current_fingerprint(target.path)
        conflict = detect_conflict(before, after)
        if conflict is not None:
            if run_id is not None and not dry_run:
                self._record_conflict(run_id, conflict, connection=connection)
            return OrchestrationResult(
                status="conflict", applied=False, changed=True, conflict=conflict, dry_run=dry_run
            )

        if dry_run:
            return OrchestrationResult(status="dry-run", applied=False, changed=True, dry_run=True)

        return self._apply_and_record(
            run_id=run_id,
            spec_hash=spec_hash,
            target=target,
            document=document,
            operations=plan.operations,
            original_exists=True,
            adapter=adapter,
            event_type="applied",
            connection=connection,
        )

    def _record_conflict(
        self,
        run_id: int,
        conflict: ConflictResult,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        self.state_store.record_event(
            run_id=run_id,
            event_type="conflict",
            path=conflict.path,
            changed=True,
            summary=conflict.reason,
            details=_conflict_details(conflict),
            connection=connection,
        )

    def _apply_and_record(
        self,
        *,
        run_id: int | None,
        spec_hash: bytes,
        target: SpecTarget,
        document: Document,
        operations: list[PlannedOperation],
        original_exists: bool,
        adapter: Adapter,
        event_type: str,
        connection: sqlite3.Connection | None = None,
    ) -> OrchestrationResult:
        apply_operations(document, operations)
        adapter.dump(document, target.path)
        written_text = target.path.read_text(encoding="utf-8")
        new_stat = target.path.stat()
        new_hash = sha256_bytes(target.path.read_bytes())
        assert run_id is not None
        self.state_store.record_checkpoint(
            run_id=run_id,
            path=target.path,
            content_text=written_text,
            format=target.format,
            original_exists=original_exists,
            connection=connection,
        )
        self.state_store.record_snapshot(
            run_id=run_id,
            path=target.path,
            content_hash=new_hash,
            size=new_stat.st_size,
            mtime_ns=new_stat.st_mtime_ns,
            format=target.format,
            spec_hash=spec_hash,
            connection=connection,
        )
        self.state_store.record_change_batch(
            run_id=run_id,
            path=target.path,
            operations=[_operation_to_data(op) for op in operations],
            original_exists=original_exists,
            format=target.format,
            connection=connection,
        )
        self.state_store.record_event(
            run_id=run_id,
            event_type=event_type,
            path=target.path,
            changed=True,
            summary=f"{event_type} with {len(operations)} ops",
            connection=connection,
        )
        return OrchestrationResult(status="applied", applied=True, changed=True)


def target_to_desired(target: SpecTarget) -> DesiredState:
    return DesiredState(
        path=target.path,
        format=target.format,
        data=target.data,
        delete=target.delete,
        managed_block_id=target.managed_block_id,
        lines=target.lines,
    )


def _empty_document(path: Path, fmt: str) -> Document:
    if fmt in {"toml", "yaml", "json5", "jsonc"}:
        return Document(path=path, format=fmt, root={})
    if fmt == "line":
        from prescribe.adapters.line import LineDocument, preferred_newline

        return Document(path=path, format="line", root=LineDocument(path=path, newline=preferred_newline(path)))
    raise ValueError(f"unsupported format for empty document: {fmt}")


def _current_fingerprint(path: Path) -> FileFingerprint:
    stat = path.stat()
    content_hash = sha256_bytes(path.read_bytes())
    return file_fingerprint(path, "sha256", content_hash, stat.st_size, stat.st_mtime_ns)


def _conflict_details(conflict: ConflictResult) -> str:
    baseline = _fingerprint_details(conflict.baseline_fingerprint)
    current = _fingerprint_details(conflict.current_fingerprint)
    return f"baseline={baseline}; current={current}"


def _fingerprint_details(fingerprint: FileFingerprint | None) -> str:
    if fingerprint is None:
        return "none"
    return (
        f"path={fingerprint.path} "
        f"algo={fingerprint.hash_algo} "
        f"hash={fingerprint.content_hash.hex()} "
        f"size={fingerprint.size} "
        f"mtime_ns={fingerprint.mtime_ns}"
    )


def _managed_state_from_batches(
    batches: list[ChangeBatchRecord],
) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for batch in batches:
        for operation in batch.operations:
            key = operation.get("key")
            if key is None:
                continue
            kind = operation.get("kind")
            if kind in {"set", "update", "replace_block"}:
                state[str(key)] = {"exists": True, "value": operation.get("value")}
                continue
            if kind in {"delete", "delete_block"}:
                state[str(key)] = {"exists": False, "value": None}
    return state


def _detect_managed_conflict(
    document: Document,
    operations: list[PlannedOperation],
    managed_state: dict[str, dict[str, Any]],
    *,
    baseline_fingerprint: FileFingerprint | None,
    current_fingerprint: FileFingerprint,
) -> ConflictResult | None:
    for operation in operations:
        if operation.key is None:
            continue
        key = str(operation.key)
        if key not in managed_state:
            continue
        expected = managed_state[key]
        current = _current_operation_state(document, key)
        if current != expected:
            return ConflictResult(
                path=document.path,
                changed=True,
                reason=f"managed key {key!r} changed externally",
                baseline_fingerprint=baseline_fingerprint,
                current_fingerprint=current_fingerprint,
            )
    return None


def _current_operation_state(document: Document, key: str) -> dict[str, Any]:
    if getattr(document, "format", None) == "line":
        block = document.root.block(key)
        if block is None:
            return {"exists": False, "value": None}
        return {"exists": True, "value": [line.rstrip("\r\n") for line in block.lines]}

    value = mapping_value(document.root, key)
    if value is _MISSING:
        return {"exists": False, "value": None}
    return {"exists": True, "value": _jsonable(value)}


def _operation_to_data(operation: PlannedOperation) -> dict[str, Any]:
    return {
        "kind": operation.kind,
        "key": operation.key,
        "value": _jsonable(operation.value),
        "before_value": _jsonable(operation.before_value),
        "before_exists": operation.before_exists,
        "reason": operation.reason,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value
