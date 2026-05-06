import contextlib
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

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
from prescribe.rollback import ConflictResolver, perform_rollback
from prescribe.shell import render_shell_block
from prescribe.spec import EnvTarget, FileTarget, ShellTarget, Spec
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


def condition_matches(
    *,
    target: FileTarget | EnvTarget | ShellTarget,
    tags: set[str] | None = None,
    skip_tags: set[str] | None = None,
) -> bool:
    """Check all gating conditions for a target. True = should process."""
    return condition_skip_reason(target=target, tags=tags, skip_tags=skip_tags) is None


def condition_skip_reason(
    *,
    target: FileTarget | EnvTarget | ShellTarget,
    tags: set[str] | None = None,
    skip_tags: set[str] | None = None,
) -> str | None:
    if not platform_matches(target.platforms):
        return "platform did not match"
    if not machine_matches(target.machine):
        return "machine did not match"

    target_tags = getattr(target, "tags", None) or []
    if tags is not None and not (tags & set(target_tags)):
        return "tags did not match"
    if skip_tags is not None and (skip_tags & set(target_tags)):
        return "skip-tags matched"

    if hasattr(target, "if_command_exists"):
        for cmd in getattr(target, "if_command_exists", []):
            if shutil.which(cmd) is None:
                return f"command not found: {cmd}"

    if hasattr(target, "if_env_missing") and getattr(target, "if_env_missing", False):
        name = getattr(target, "name", None)
        if name and name in os.environ:
            return f"env var already set: {name}"

    return None


def _spec_hash(spec: Spec) -> bytes:
    """Compute a deterministic content hash for a Spec object."""
    data = {
        "id": spec.id,
        "files": [
            {
                "path": str(f.path),
                "format": f.format,
                "data": f.data,
                "delete": f.delete,
                "managed_block_id": f.managed_block_id,
                "lines": f.lines,
            }
            for f in spec.files
        ],
        "env": [{"name": e.name, "value": e.value} for e in spec.env],
        "shell": [{"path": str(s.path), "managed_block_id": s.managed_block_id} for s in spec.shell],
    }
    return sha256_bytes(json.dumps(data, sort_keys=True, default=str).encode())


class Orchestrator:
    def __init__(
        self,
        state_store: StateStore,
        *,
        _materialize_fn: Callable[..., None] | None = None,
    ) -> None:
        self.state_store = state_store
        self.planner = Planner()
        self._materialize_fn = _materialize_fn

    def run(
        self,
        spec: Path | str | Spec,
        *,
        tool_version: str | None = None,
        dry_run: bool = False,
        tags: set[str] | None = None,
        skip_tags: set[str] | None = None,
        diff: bool = False,
        explain_skips: bool = False,
    ) -> list[OrchestrationResult]:
        from prescribe.spec import SpecLoader

        if isinstance(spec, (Path, str)):
            spec_path = Path(spec)
            spec_obj = SpecLoader().load(spec_path)
            spec_hash = sha256_bytes(spec_path.read_bytes())
        else:
            spec_obj = spec
            spec_hash = _spec_hash(spec_obj)

        if dry_run:
            self.state_store.initialize()
            return self._run_all(
                run_id=None,
                spec_hash=spec_hash,
                spec=spec_obj,
                dry_run=True,
                tags=tags,
                skip_tags=skip_tags,
                diff=diff,
                explain_skips=explain_skips,
            )

        self.state_store.initialize()
        with self.state_store.transaction() as connection:
            run = self.state_store.start_run(
                spec_hash=spec_hash,
                tool_version=tool_version,
                platform=sys.platform,
                host=current_machine(),
                connection=connection,
            )
            return self._run_all(
                run_id=run.id,
                spec_hash=spec_hash,
                spec=spec_obj,
                dry_run=False,
                tags=tags,
                skip_tags=skip_tags,
                diff=diff,
                explain_skips=explain_skips,
                connection=connection,
            )

    def _run_all(
        self,
        run_id: int | None,
        spec_hash: bytes,
        spec: Spec,
        *,
        dry_run: bool = False,
        tags: set[str] | None = None,
        skip_tags: set[str] | None = None,
        diff: bool = False,
        explain_skips: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> list[OrchestrationResult]:
        results: list[OrchestrationResult] = []

        # ── files ──
        # Resolve active vs skipped, but produce results in spec.files order
        # so that CLI/JSON pairing stays aligned.
        active_files, skipped_files = _resolve_active_files(spec.files, tags=tags, skip_tags=skip_tags)
        for file_target in spec.files:
            if file_target in skipped_files or file_target not in active_files:
                reason = condition_skip_reason(target=file_target, tags=tags, skip_tags=skip_tags)
                if reason is None:
                    reason = "lower priority target selected"
                results.append(
                    OrchestrationResult(
                        status="skipped",
                        applied=False,
                        changed=False,
                        skipped=True,
                        skip_reason=reason if explain_skips else None,
                    )
                )
                continue
            results.append(
                self._handle_file(
                    run_id,
                    spec_hash,
                    file_target,
                    dry_run=dry_run,
                    tags=tags,
                    skip_tags=skip_tags,
                    diff=diff,
                    explain_skips=explain_skips,
                    connection=connection,
                )
            )

        # ── env ──
        resolved_env, materialize_names = self._resolve_env(spec.env, tags=tags, skip_tags=skip_tags)

        # Materialize env vars to OS-level store
        materialize_errors: list[str] = []
        if not dry_run and materialize_names:
            mat_env = {k: v for k, v in resolved_env.items() if k in materialize_names}
            materialize_errors.extend(self._do_materialize(mat_env))

        # Produce an env result so callers can inspect resolved vars
        # even when there are no [[files]] or [[shell]] targets
        if spec.env and not spec.files and not spec.shell:
            results.append(
                OrchestrationResult(
                    status="applied" if not dry_run else "dry-run",
                    applied=not dry_run,
                    changed=bool(resolved_env),
                    env_vars=resolved_env,
                    dry_run=dry_run,
                )
            )

        # ── shell blocks ──
        for shell_target in spec.shell:
            shell_type = _shell_type_for_target(shell_target)
            shell_env, _ = self._resolve_env(spec.env, tags=tags, skip_tags=skip_tags, shell_type=shell_type)
            result = self._handle_shell(
                run_id,
                spec_hash,
                shell_target,
                shell_env,
                dry_run=dry_run,
                tags=tags,
                skip_tags=skip_tags,
                diff=diff,
                explain_skips=explain_skips,
                connection=connection,
            )
            result.env_vars = shell_env
            results.append(result)

        # Attach env vars and materialize errors to all results for convenience
        for r in results:
            if not r.env_vars:
                r.env_vars = resolved_env
            if materialize_errors:
                r.materialize_errors = materialize_errors

        return results

    # ── file handling ─────────────────────────────────

    def _handle_file(
        self,
        run_id: int | None,
        spec_hash: bytes,
        target: FileTarget,
        *,
        dry_run: bool = False,
        tags: set[str] | None = None,
        skip_tags: set[str] | None = None,
        diff: bool = False,
        explain_skips: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> OrchestrationResult:
        if not condition_matches(target=target, tags=tags, skip_tags=skip_tags):
            return OrchestrationResult(
                status="skipped",
                applied=False,
                changed=False,
                skipped=True,
                skip_reason=condition_skip_reason(target=target, tags=tags, skip_tags=skip_tags)
                if explain_skips
                else None,
            )

        adapter = adapter_for_path(target.path, fmt=target.format)

        try:
            if not target.path.exists():
                return self._process_new_file(
                    run_id,
                    spec_hash,
                    target,
                    adapter,
                    dry_run=dry_run,
                    diff=diff,
                    connection=connection,
                )
            return self._process_existing_file(
                run_id,
                spec_hash,
                target,
                adapter,
                dry_run=dry_run,
                diff=diff,
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
        target: FileTarget,
        adapter: Adapter,
        *,
        dry_run: bool = False,
        diff: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> OrchestrationResult:
        document = _empty_document(target.path, target.format)
        plan = self.planner.plan(document, _file_desired(target))
        if not plan.changed:
            return OrchestrationResult(
                status="dry-run" if dry_run else "noop",
                applied=False,
                changed=False,
                dry_run=dry_run,
            )

        diff_text = _maybe_diff_new(plan, adapter, target.path) if diff else None

        if dry_run:
            return OrchestrationResult(status="dry-run", applied=False, changed=True, dry_run=True, diff=diff_text)

        if target.path.exists():
            if run_id is None:
                raise RuntimeError("run_id is None in _process_new_file")
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
            original_text=None,
        )

    def _process_existing_file(
        self,
        run_id: int | None,
        spec_hash: bytes,
        target: FileTarget,
        adapter: Adapter,
        *,
        dry_run: bool = False,
        diff: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> OrchestrationResult:
        before = _current_fingerprint(target.path)
        document = adapter.load(target.path)
        plan = self.planner.plan(document, _file_desired(target))
        managed_conflict: ConflictResult | None = None
        if run_id is not None or dry_run:
            ctx = self.state_store.connect() if connection is None else contextlib.nullcontext(connection)
            with ctx as read_conn:
                managed_conflict = self._check_managed_conflict(document, plan, target, before, read_conn)

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

        diff_text = _maybe_diff(document, plan, adapter, target.path) if diff else None

        after = _current_fingerprint(target.path)
        conflict = detect_conflict(before, after)
        if conflict is not None:
            if run_id is not None and not dry_run:
                self._record_conflict(run_id, conflict, connection=connection)
            return OrchestrationResult(
                status="conflict", applied=False, changed=True, conflict=conflict, dry_run=dry_run
            )

        if dry_run:
            return OrchestrationResult(status="dry-run", applied=False, changed=True, dry_run=True, diff=diff_text)

        original_text = target.path.read_text(encoding="utf-8")
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
            original_text=original_text,
        )

    # ── env resolution ────────────────────────────────

    def _resolve_env(
        self,
        env_targets: list[EnvTarget],
        *,
        tags: set[str] | None = None,
        skip_tags: set[str] | None = None,
        shell_type: str | None = None,
    ) -> tuple[dict[str, str], set[str]]:
        """Filter, merge, and resolve env targets into a flat name→value dict.

        Merging rules for duplicate names:
        - value: last one wins
        - prepend: accumulated in spec order (earlier entries prepend first)
        - append: accumulated in spec order
        - path_prepend/path_append: forwarded to PATH construction
        - materialize: True if any entry says True

        Returns (resolved_dict, materialize_names).
        """
        path_prepends: dict[str, list[str]] = {}
        path_appends: dict[str, list[str]] = {}
        resolved: dict[str, str] = {}
        materialize_names: set[str] = set()

        # Build a local env context: os.environ + progressively resolved values
        local_env: dict[str, str] = dict(os.environ)

        for et in env_targets:
            if not condition_matches(target=et, tags=tags, skip_tags=skip_tags):
                continue
            if shell_type is not None and et.shells and shell_type not in et.shells:
                continue

            if et.materialize:
                materialize_names.add(et.name)

            if et.path_prepend:
                expanded_entries = [_expand_tilde(e) for e in et.path_prepend]
                path_prepends.setdefault(et.name, []).extend(expanded_entries)
                path_prepends.setdefault("PATH", []).extend(expanded_entries)
            if et.path_append:
                expanded_entries = [_expand_tilde(e) for e in et.path_append]
                path_appends.setdefault(et.name, []).extend(expanded_entries)
                path_appends.setdefault("PATH", []).extend(expanded_entries)

            # Collect prepend/append for PATH
            if et.prepend:
                expanded_prepends = [_expand_tilde(e) for e in et.prepend]
                path_prepends.setdefault("PATH", []).extend(expanded_prepends)
            if et.append:
                expanded_appends = [_expand_tilde(e) for e in et.append]
                path_appends.setdefault("PATH", []).extend(expanded_appends)

            # Value: last one wins. Expand against local_env so later entries
            # can reference earlier ones (e.g., CARGO_HOME → PATH prepend)
            if et.value is not None:
                expanded = _expandvars(et.value, local_env)
                expanded = _expand_tilde(expanded)
                resolved[et.name] = expanded
                local_env[et.name] = expanded

        # Build PATH from prepends + existing + appends
        if "PATH" in path_prepends or "PATH" in path_appends:
            current_path = local_env.get("PATH", "")
            current_entries = [p for p in current_path.split(os.pathsep) if p]

            prepend_entries = path_prepends.get("PATH", [])
            append_entries = path_appends.get("PATH", [])

            # Deduplicate prepend entries: keep first occurrence
            seen: set[str] = set()
            deduped_prepends: list[str] = []
            for entry in prepend_entries:
                expanded = _normalize_path_entry(_expandvars(entry, local_env))
                if expanded not in seen:
                    seen.add(expanded)
                    deduped_prepends.append(expanded)

            # Deduplicate append entries
            deduped_appends: list[str] = []
            for entry in append_entries:
                expanded = _normalize_path_entry(_expandvars(entry, local_env))
                if expanded not in seen:
                    seen.add(expanded)
                    deduped_appends.append(expanded)

            deduped_current: list[str] = []
            for entry in current_entries:
                if entry not in seen:
                    seen.add(entry)
                    deduped_current.append(entry)

            new_path = deduped_prepends + deduped_current + deduped_appends
            resolved["PATH"] = os.pathsep.join(new_path)

        return resolved, materialize_names

    # ── shell handling ────────────────────────────────

    def _handle_shell(
        self,
        run_id: int | None,
        spec_hash: bytes,
        target: ShellTarget,
        resolved_env: dict[str, str],
        *,
        dry_run: bool = False,
        tags: set[str] | None = None,
        skip_tags: set[str] | None = None,
        diff: bool = False,
        explain_skips: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> OrchestrationResult:
        if not condition_matches(target=target, tags=tags, skip_tags=skip_tags):
            return OrchestrationResult(
                status="skipped",
                applied=False,
                changed=False,
                skipped=True,
                skip_reason=condition_skip_reason(target=target, tags=tags, skip_tags=skip_tags)
                if explain_skips
                else None,
            )

        rendered = render_shell_block(
            shell_type=_shell_type_for_target(target),
            env_vars=dict(resolved_env),
            managed_block_id=target.managed_block_id,
        )

        return self._apply_shell_block(
            run_id=run_id,
            spec_hash=spec_hash,
            target=target,
            rendered_lines=rendered,
            dry_run=dry_run,
            diff=diff,
            connection=connection,
        )

    def _apply_shell_block(
        self,
        run_id: int | None,
        spec_hash: bytes,
        target: ShellTarget,
        rendered_lines: list[str],
        *,
        dry_run: bool = False,
        diff: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> OrchestrationResult:
        """Apply a shell block using the LINE adapter + managed block pattern."""
        adapter = adapter_for_path(target.path, fmt="line")

        try:
            document = adapter.load(target.path) if target.path.exists() else _empty_document(target.path, "line")

            desired = DesiredState(
                path=target.path,
                format="line",
                managed_block_id=target.managed_block_id,
                lines=rendered_lines,
            )

            plan = self.planner.plan(document, desired)

            if not plan.changed:
                if run_id is not None and not dry_run:
                    self.state_store.record_checked(
                        run_id=run_id,
                        path=target.path,
                        content_hash=sha256_bytes(target.path.read_bytes()),
                        size=target.path.stat().st_size,
                        mtime_ns=target.path.stat().st_mtime_ns,
                        format="line",
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

            diff_text = _maybe_diff(document, plan, adapter, target.path) if diff else None

            if dry_run:
                return OrchestrationResult(
                    status="dry-run", applied=False, changed=True, dry_run=True, diff=diff_text
                )

            if run_id is None:
                raise RuntimeError("run_id is None in _apply_shell_block")
            original_text = target.path.read_text(encoding="utf-8") if target.path.exists() else ""
            original_exists = target.path.exists()

            return self._apply_and_record(
                run_id=run_id,
                spec_hash=spec_hash,
                target=target,
                document=document,
                operations=plan.operations,
                original_exists=original_exists,
                adapter=adapter,
                event_type="applied",
                connection=connection,
                original_text=original_text if original_exists else None,
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

    # ── rollback ──────────────────────────────────────

    def _do_materialize(self, env_vars: dict[str, str]) -> list[str]:
        """Write env vars to the OS-level persistent store.

        Returns a list of error strings (empty if successful).
        """
        from prescribe.materialize import materialize

        try:
            if self._materialize_fn is not None:
                self._materialize_fn(env_vars=env_vars, dry_run=False)
            else:
                materialize(env_vars=env_vars, dry_run=False)
        except Exception as exc:
            return [str(exc)]
        return []

    def rollback(
        self,
        target_path: Path | str,
        *,
        dry_run: bool = False,
        original: bool = False,
        conflict_resolver: ConflictResolver = None,
    ) -> OrchestrationResult:
        path = Path(target_path)
        if not dry_run:
            self.state_store.initialize()
        elif not self.state_store.path.exists():
            return OrchestrationResult(status="noop", applied=False, changed=False, dry_run=True)

        if not dry_run:
            with self.state_store.transaction() as connection:
                return perform_rollback(
                    path,
                    self.state_store,
                    dry_run=False,
                    original=original,
                    resolver=conflict_resolver,
                    connection=connection,
                )
        return perform_rollback(path, self.state_store, dry_run=True, original=original, resolver=conflict_resolver)

    # ── conflict helpers ──────────────────────────────

    def _check_managed_conflict(
        self,
        document: Document,
        plan: Any,
        target: FileTarget,
        current_fingerprint: "FileFingerprint",
        connection: sqlite3.Connection,
    ) -> ConflictResult | None:
        managed_state = _managed_state_from_batches(self.state_store.change_batches(target.path, connection=connection))
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
        return _detect_managed_conflict(
            document,
            plan.operations,
            managed_state,
            baseline_fingerprint=baseline,
            current_fingerprint=current_fingerprint,
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
        target: FileTarget | ShellTarget,
        document: Document,
        operations: list[PlannedOperation],
        original_exists: bool,
        adapter: Adapter,
        event_type: str,
        connection: sqlite3.Connection | None = None,
        original_text: str | None = None,
    ) -> OrchestrationResult:
        apply_operations(document, operations)
        adapter.dump(document, target.path)
        new_bytes = target.path.read_bytes()
        written_text = new_bytes.decode("utf-8")
        new_stat = target.path.stat()
        new_hash = sha256_bytes(new_bytes)
        if run_id is None:
            raise RuntimeError("run_id is None in _apply_and_record")
        existing_baseline = self.state_store.original_baseline(target.path, connection=connection)
        if existing_baseline is None:
            self.state_store.record_baseline(
                run_id=run_id,
                path=target.path,
                content_text=original_text or "",
                format=getattr(target, "format", "line"),
                original_exists=original_exists,
                connection=connection,
            )
        self.state_store.record_checkpoint(
            run_id=run_id,
            path=target.path,
            content_text=written_text,
            format=getattr(target, "format", "line"),
            original_exists=original_exists,
            connection=connection,
        )
        self.state_store.record_snapshot(
            run_id=run_id,
            path=target.path,
            content_hash=new_hash,
            size=new_stat.st_size,
            mtime_ns=new_stat.st_mtime_ns,
            format=getattr(target, "format", "line"),
            spec_hash=spec_hash,
            connection=connection,
        )
        self.state_store.record_change_batch(
            run_id=run_id,
            path=target.path,
            operations=[_operation_to_data(op) for op in operations],
            original_exists=original_exists,
            format=getattr(target, "format", "line"),
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


# ── module-level helpers ──────────────────────────────────


def _file_desired(target: FileTarget) -> DesiredState:
    return DesiredState(
        path=target.path,
        format=target.format,
        data=target.data,
        delete=target.delete,
        managed_block_id=target.managed_block_id,
        lines=target.lines,
    )


def _shell_type_for_target(target: ShellTarget) -> str:
    render_shells = target.shells if target.shells else ["bash"]
    return render_shells[0]


def _empty_document(path: Path, fmt: str) -> Document:
    if fmt in {"toml", "yaml", "jsonc"}:
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


# ── diff helpers ───────────────────────────────────────────


def _expandvars(value: str, env: dict[str, str]) -> str:
    """Expand $VAR and ${VAR} in a string using the given env dict."""

    def replacer(match: re.Match[str]) -> str:
        var = match.group(1) or match.group(2)
        return env.get(var, f"${var}")

    return re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)|\$\{([^}]+)\}", replacer, value)


def _expand_tilde(value: str) -> str:
    """Expand ~ to home directory."""
    if value == "~" or value.startswith("~/"):
        home = Path(os.environ.get("HOME") or Path.home())
        return str(home / value[2:]) if value.startswith("~/") else str(home)
    return value


def _normalize_path_entry(value: str) -> str:
    if "$" in value or value.startswith("!"):
        return value
    return str(Path(value))


def _resolve_active_files(
    file_targets: list[FileTarget],
    *,
    tags: set[str] | None = None,
    skip_tags: set[str] | None = None,
) -> tuple[list[FileTarget], list[FileTarget]]:
    """Partition and deduplicate files.

    Returns (active, skipped) where active is deduplicated by priority.
    skipped contains files that didn't pass condition_matches.
    """
    active_candidates: list[FileTarget] = []
    skipped: list[FileTarget] = []

    for ft in file_targets:
        if condition_matches(target=ft, tags=tags, skip_tags=skip_tags):
            active_candidates.append(ft)
        else:
            skipped.append(ft)

    # Deduplicate active candidates by path: lowest priority, first-in-spec breaks ties
    seen: dict[Path, FileTarget] = {}
    for ft in active_candidates:
        existing = seen.get(ft.path)
        if existing is None or ft.priority < existing.priority:
            seen[ft.path] = ft

    return list(seen.values()), skipped


def _maybe_diff(
    document: Document,
    plan: Any,
    adapter: Adapter,
    path: Path,
) -> str | None:
    """Generate a diff for an existing file if it would change."""
    from prescribe.diff import diff_file

    return diff_file(document=document, plan=plan.operations, adapter=adapter, path=path)


def _maybe_diff_new(
    plan: Any,
    adapter: Adapter,
    path: Path,
) -> str | None:
    """Generate a diff for a new file."""
    from prescribe.diff import diff_new_file

    return diff_new_file(plan=plan.operations, adapter=adapter, path=path)
