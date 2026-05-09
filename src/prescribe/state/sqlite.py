import json
import os
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from prescribe.state.engine import engine_for_connection_factory, initialize_engine, session_scope
from prescribe.state.filesystem import (
    NetworkStatePathError,
    is_network_filesystem_path,
    network_state_allowed,
)
from prescribe.state.migrate import migrate as migrate_schema
from prescribe.state.models import ManagedClaim, RunLock, SpecRun, TargetRun


@dataclass(slots=True)
class RunRecord:
    id: int
    started_at: datetime
    spec_hash: bytes | None
    tool_version: str | None
    host: str | None
    platform: str | None
    ended_at: datetime | None = None
    status: str | None = None
    command: str | None = None
    cwd: str | None = None


@dataclass(slots=True)
class SnapshotRecord:
    id: int
    run_id: int
    path: Path
    captured_at: datetime
    hash_algo: str
    content_hash: bytes
    size: int
    mtime_ns: int | None
    format: str | None
    spec_hash: bytes | None


@dataclass(slots=True)
class CheckpointRecord:
    id: int
    run_id: int
    path: Path
    captured_at: datetime
    format: str | None
    content_text: str
    original_exists: bool


@dataclass(slots=True)
class EventRecord:
    id: int
    run_id: int
    created_at: datetime
    event_type: str
    path: Path | None
    changed: bool
    summary: str | None
    details: str | None


@dataclass(slots=True)
class ChangeBatchRecord:
    id: int
    run_id: int
    path: Path
    created_at: datetime
    format: str | None
    original_exists: bool
    operations: list[dict[str, Any]]


@dataclass(slots=True)
class ManagedRecord:
    path: Path
    format: str | None
    last_applied_at: datetime


@dataclass(slots=True)
class BaselineRecord:
    id: int
    run_id: int
    path: Path
    captured_at: datetime
    format: str | None
    content_text: str
    original_exists: bool


@dataclass(slots=True)
class AssetBackupRecord:
    id: int
    run_id: int
    target_dest: Path
    original_path: Path
    backup_path: Path
    created_at: datetime
    hash_algo: str
    content_hash: bytes
    size: int
    mtime_ns: int | None
    file_type: str
    restored_at: datetime | None


@dataclass(slots=True)
class RecoveryBackupRecord:
    id: int
    run_id: int
    target_path: Path
    target_kind: str
    operation: str
    backup_path: Path
    created_at: datetime
    hash_algo: str
    content_hash: bytes
    size: int
    mtime_ns: int | None
    content_text: str | None


@dataclass(slots=True)
class RunLockRecord:
    name: str
    owner: str
    token: str | None
    run_id: int | None
    acquired_at: datetime
    heartbeat_at: datetime | None
    expires_at: datetime


@dataclass(slots=True)
class ManagedClaimRecord:
    id: int
    target_type: str
    subject: str
    address: str
    owner_id: str
    spec_path: str | None
    target_id: str | None
    created_at: datetime
    last_seen_at: datetime


@dataclass(slots=True)
class ClaimReservationRecord:
    id: int
    target_type: str
    subject: str
    address: str
    owner_id: str
    run_id: int | None
    token: str
    status: str
    created_at: datetime
    expires_at: datetime


@dataclass(slots=True)
class TargetAttemptRecord:
    id: int
    run_id: int
    target_type: str
    target_id: str | None
    subject: str
    address: str
    owner_id: str
    phase: str
    started_at: datetime
    updated_at: datetime
    error: str | None


@dataclass(slots=True)
class SpecRunRecord:
    id: int
    run_id: int
    spec_path: Path
    spec_hash: bytes | None
    order_index: int
    valid: bool


@dataclass(slots=True)
class TargetRunRecord:
    id: int
    run_id: int
    spec_run_id: int | None
    target_type: str
    target_id: str | None
    path_or_name: str
    status: str
    skip_reason: str | None
    changed: bool


class StateStore:
    def __init__(
        self,
        path: Path | str,
        *,
        connection_factory: Callable[[Path], sqlite3.Connection] = sqlite3.connect,
        allow_network_state: bool = False,
    ) -> None:
        raw_path = Path(path)
        self.path = raw_path if str(raw_path) == ":memory:" else _canonical_path(raw_path)
        if not allow_network_state and not network_state_allowed() and is_network_filesystem_path(self.path):
            raise NetworkStatePathError(
                f"refusing to use SQLite state database on a network filesystem: {self.path}. "
                "Move the state database to a local disk or set PRESCRIBE_ALLOW_NETWORK_STATE=1."
            )
        self.connection_factory = connection_factory
        self._engine = engine_for_connection_factory(path=self.path, connection_factory=self._open_for_engine)

    def _open_for_engine(self, path: Path) -> sqlite3.Connection:
        if str(path) != ":memory:" and "file:" not in str(path):
            os.makedirs(path.parent, exist_ok=True)
        connection = self.connection_factory(path)
        connection.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:" and "file:" not in str(path):
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _open(self) -> sqlite3.Connection:
        if str(self.path) != ":memory:":
            os.makedirs(self.path.parent, exist_ok=True)
        connection = self.connection_factory(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        if str(self.path) != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def connect(self) -> sqlite3.Connection:
        return self._open()

    @contextmanager
    def _connection(self, connection: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
        if connection is not None:
            yield connection
        else:
            conn = self._open()
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._open()
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self, *, connection: sqlite3.Connection | None = None, dry_run: bool = False) -> list[str]:
        with self._connection(connection) as conn:
            return migrate_schema(conn, dry_run=dry_run)

    @contextmanager
    def orm_session(self) -> Iterator[Session]:
        initialize_engine(self._engine)
        with session_scope(self._engine) as session:
            yield session

    def start_run(
        self,
        *,
        spec_hash: bytes | None = None,
        tool_version: str | None = None,
        host: str | None = None,
        platform: str | None = None,
        command: str | None = None,
        cwd: Path | str | None = None,
        started_at: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> RunRecord:
        started = _utcnow(started_at)
        with self._connection(connection) as conn:
            cursor = conn.execute(
                """
                INSERT INTO runs (started_at, spec_hash, tool_version, host, platform, status, command, cwd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    started.isoformat(),
                    spec_hash,
                    tool_version,
                    host,
                    platform,
                    "running",
                    command,
                    None if cwd is None else str(cwd),
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("INSERT into runs did not produce a rowid")
            run_id = cursor.lastrowid
        return RunRecord(
            id=run_id,
            started_at=started,
            spec_hash=spec_hash,
            tool_version=tool_version,
            host=host,
            platform=platform,
            status="running",
            command=command,
            cwd=None if cwd is None else str(cwd),
        )

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        ended_at: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        ended = _utcnow(ended_at)
        with self._connection(connection) as conn:
            conn.execute("UPDATE runs SET ended_at = ?, status = ? WHERE id = ?", (ended.isoformat(), status, run_id))

    def acquire_lock(
        self,
        name: str,
        *,
        owner: str,
        run_id: int | None = None,
        ttl: timedelta = timedelta(minutes=15),
        token: str | None = None,
        now: datetime | None = None,
    ) -> RunLockRecord:
        current_time = _utcnow(now)
        expires_at = current_time + ttl
        lock_token = token or uuid.uuid4().hex
        with self.orm_session() as session:
            existing = session.get(RunLock, name)
            if existing is not None and _parse_datetime(str(existing.expires_at)) > current_time:
                return _run_lock_record(existing)
            if existing is None:
                existing = RunLock(name=name)
                session.add(existing)
            existing_any: Any = existing
            existing_any.owner = owner
            existing_any.token = lock_token
            existing_any.run_id = run_id
            existing_any.acquired_at = current_time.isoformat()
            existing_any.heartbeat_at = current_time.isoformat()
            existing_any.expires_at = expires_at.isoformat()
            session.flush()
            return _run_lock_record(existing)

    def active_lock(self, name: str) -> RunLockRecord | None:
        now = _utcnow()
        with self.orm_session() as session:
            existing = session.get(RunLock, name)
            if existing is None:
                return None
            record = _run_lock_record(existing)
            return record if record.expires_at > now else None

    def heartbeat_lock(
        self,
        name: str,
        *,
        owner: str,
        token: str,
        ttl: timedelta = timedelta(minutes=15),
        now: datetime | None = None,
    ) -> RunLockRecord | None:
        current_time = _utcnow(now)
        expires_at = current_time + ttl
        with self.orm_session() as session:
            existing = session.get(RunLock, name)
            if existing is None or existing.owner != owner or existing.token != token:
                return None
            if _parse_datetime(str(existing.expires_at)) <= current_time:
                return None
            existing_any: Any = existing
            existing_any.heartbeat_at = current_time.isoformat()
            existing_any.expires_at = expires_at.isoformat()
            session.flush()
            return _run_lock_record(existing)

    def lock_is_current(self, name: str, *, owner: str, token: str, now: datetime | None = None) -> bool:
        current_time = _utcnow(now)
        with self.orm_session() as session:
            existing = session.get(RunLock, name)
            if existing is None or existing.owner != owner or existing.token != token:
                return False
            return _parse_datetime(str(existing.expires_at)) > current_time

    def release_lock(self, name: str, *, owner: str | None = None, token: str | None = None) -> None:
        with self.orm_session() as session:
            existing = session.get(RunLock, name)
            if existing is None:
                return
            if owner is not None and existing.owner != owner:
                return
            if token is not None and existing.token != token:
                return
            session.delete(existing)

    def record_spec_run(
        self,
        *,
        run_id: int,
        spec_path: Path | str,
        spec_hash: bytes | None,
        order_index: int,
        valid: bool,
        connection: sqlite3.Connection | None = None,
    ) -> SpecRunRecord:
        if connection is not None:
            cursor = connection.execute(
                """
                INSERT INTO spec_runs (run_id, spec_path, spec_hash, order_index, valid)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, str(spec_path), spec_hash, order_index, int(valid)),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("INSERT into spec_runs did not produce a rowid")
            return SpecRunRecord(
                id=cursor.lastrowid,
                run_id=run_id,
                spec_path=Path(str(spec_path)),
                spec_hash=spec_hash,
                order_index=order_index,
                valid=valid,
            )
        with self.orm_session() as session:
            row = SpecRun(
                run_id=run_id,
                spec_path=str(spec_path),
                spec_hash=spec_hash,
                order_index=order_index,
                valid=valid,
            )
            session.add(row)
            session.flush()
            return _spec_run_record(row)

    def record_target_run(
        self,
        *,
        run_id: int,
        target_type: str,
        path_or_name: str,
        status: str,
        changed: bool,
        spec_run_id: int | None = None,
        target_id: str | None = None,
        skip_reason: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> TargetRunRecord:
        if connection is not None:
            cursor = connection.execute(
                """
                INSERT INTO target_runs (
                    run_id, spec_run_id, target_type, target_id, path_or_name, status, skip_reason, changed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    spec_run_id,
                    target_type,
                    target_id,
                    path_or_name,
                    status,
                    skip_reason,
                    int(changed),
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("INSERT into target_runs did not produce a rowid")
            return TargetRunRecord(
                id=cursor.lastrowid,
                run_id=run_id,
                spec_run_id=spec_run_id,
                target_type=target_type,
                target_id=target_id,
                path_or_name=path_or_name,
                status=status,
                skip_reason=skip_reason,
                changed=changed,
            )
        with self.orm_session() as session:
            row = TargetRun(
                run_id=run_id,
                spec_run_id=spec_run_id,
                target_type=target_type,
                target_id=target_id,
                path_or_name=path_or_name,
                status=status,
                skip_reason=skip_reason,
                changed=changed,
            )
            session.add(row)
            session.flush()
            return _target_run_record(row)

    def target_runs(self, run_id: int) -> list[TargetRunRecord]:
        with self.orm_session() as session:
            rows = session.scalars(select(TargetRun).where(TargetRun.run_id == run_id).order_by(TargetRun.id)).all()
            return [_target_run_record(row) for row in rows]

    def upsert_claim(
        self,
        *,
        target_type: str,
        subject: str,
        address: str,
        owner_id: str,
        spec_path: str | None = None,
        target_id: str | None = None,
        take: bool = False,
        now: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> ManagedClaimRecord:
        seen = _utcnow(now)
        if connection is not None:
            row = connection.execute(
                """
                SELECT id, target_type, subject, address, owner_id, spec_path, target_id, created_at, last_seen_at
                FROM managed_claims
                WHERE target_type = ? AND subject = ? AND address = ?
                """,
                (target_type, subject, address),
            ).fetchone()
            if row is not None:
                if row[4] != owner_id and not take:
                    return _managed_claim_record_from_row(row)
                connection.execute(
                    """
                    UPDATE managed_claims
                    SET owner_id = ?, spec_path = ?, target_id = ?, last_seen_at = ?
                    WHERE id = ?
                    """,
                    (owner_id, spec_path, target_id, seen.isoformat(), row[0]),
                )
                updated = connection.execute(
                    """
                    SELECT id, target_type, subject, address, owner_id, spec_path, target_id, created_at, last_seen_at
                    FROM managed_claims
                    WHERE id = ?
                    """,
                    (row[0],),
                ).fetchone()
                if updated is None:
                    raise RuntimeError("managed_claims row disappeared after UPDATE")
                return _managed_claim_record_from_row(updated)
            cursor = connection.execute(
                """
                INSERT INTO managed_claims (
                    target_type, subject, address, owner_id, spec_path, target_id, created_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_type,
                    subject,
                    address,
                    owner_id,
                    spec_path,
                    target_id,
                    seen.isoformat(),
                    seen.isoformat(),
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("INSERT into managed_claims did not produce a rowid")
            return ManagedClaimRecord(
                id=cursor.lastrowid,
                target_type=target_type,
                subject=subject,
                address=address,
                owner_id=owner_id,
                spec_path=spec_path,
                target_id=target_id,
                created_at=seen,
                last_seen_at=seen,
            )
        with self.orm_session() as session:
            row = session.scalar(
                select(ManagedClaim).where(
                    ManagedClaim.target_type == target_type,
                    ManagedClaim.subject == subject,
                    ManagedClaim.address == address,
                )
            )
            if row is not None:
                if row.owner_id != owner_id and not take:
                    return _managed_claim_record(row)
                row_any: Any = row
                row_any.owner_id = owner_id
                row_any.spec_path = spec_path
                row_any.target_id = target_id
                row_any.last_seen_at = seen.isoformat()
                session.flush()
                return _managed_claim_record(row)
            row = ManagedClaim(
                target_type=target_type,
                subject=subject,
                address=address,
                owner_id=owner_id,
                spec_path=spec_path,
                target_id=target_id,
                created_at=seen.isoformat(),
                last_seen_at=seen.isoformat(),
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                with self.orm_session() as retry_session:
                    existing = retry_session.scalar(
                        select(ManagedClaim).where(
                            ManagedClaim.target_type == target_type,
                            ManagedClaim.subject == subject,
                            ManagedClaim.address == address,
                        )
                    )
                    if existing is None:
                        raise
                    return _managed_claim_record(existing)
            return _managed_claim_record(row)

    def claims(self) -> list[ManagedClaimRecord]:
        with self.orm_session() as session:
            rows = session.scalars(select(ManagedClaim).order_by(ManagedClaim.subject, ManagedClaim.address)).all()
            return [_managed_claim_record(row) for row in rows]

    def reserve_claim(
        self,
        *,
        target_type: str,
        subject: str,
        address: str,
        owner_id: str,
        run_id: int | None,
        token: str,
        ttl: timedelta = timedelta(minutes=1),
        now: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> ClaimReservationRecord:
        current_time = _utcnow(now)
        expires_at = current_time + ttl
        with self._connection(connection) as conn:
            row = conn.execute(
                """
                SELECT id, target_type, subject, address, owner_id, run_id, token, status, created_at, expires_at
                FROM claim_reservations
                WHERE target_type = ? AND subject = ? AND address = ?
                """,
                (target_type, subject, address),
            ).fetchone()
            if row is not None:
                record = _claim_reservation_from_row(row)
                if record.expires_at > current_time and record.status == "reserved" and record.token != token:
                    return record
                conn.execute(
                    """
                    UPDATE claim_reservations
                    SET owner_id = ?, run_id = ?, token = ?, status = ?, created_at = ?, expires_at = ?
                    WHERE id = ?
                    """,
                    (
                        owner_id,
                        run_id,
                        token,
                        "reserved",
                        current_time.isoformat(),
                        expires_at.isoformat(),
                        record.id,
                    ),
                )
                return ClaimReservationRecord(
                    id=record.id,
                    target_type=target_type,
                    subject=subject,
                    address=address,
                    owner_id=owner_id,
                    run_id=run_id,
                    token=token,
                    status="reserved",
                    created_at=current_time,
                    expires_at=expires_at,
                )
            cursor = conn.execute(
                """
                INSERT INTO claim_reservations (
                    target_type, subject, address, owner_id, run_id, token, status, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_type,
                    subject,
                    address,
                    owner_id,
                    run_id,
                    token,
                    "reserved",
                    current_time.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("INSERT into claim_reservations did not produce a rowid")
            return ClaimReservationRecord(
                id=cursor.lastrowid,
                target_type=target_type,
                subject=subject,
                address=address,
                owner_id=owner_id,
                run_id=run_id,
                token=token,
                status="reserved",
                created_at=current_time,
                expires_at=expires_at,
            )

    def release_claim_reservations(
        self,
        *,
        run_id: int,
        token: str,
        status: str,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        with self._connection(connection) as conn:
            conn.execute(
                """
                UPDATE claim_reservations
                SET status = ?
                WHERE run_id = ? AND token = ?
                """,
                (status, run_id, token),
            )

    def claim_reservations(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[ClaimReservationRecord]:
        with self._connection(connection) as conn:
            rows = conn.execute(
                """
                SELECT id, target_type, subject, address, owner_id, run_id, token, status, created_at, expires_at
                FROM claim_reservations
                ORDER BY target_type, subject, address
                """
            ).fetchall()
        return [_claim_reservation_from_row(row) for row in rows]

    def record_target_attempt(
        self,
        *,
        run_id: int,
        target_type: str,
        subject: str,
        address: str,
        owner_id: str,
        phase: str,
        target_id: str | None = None,
        error: str | None = None,
        now: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> TargetAttemptRecord:
        timestamp = _utcnow(now)
        with self._connection(connection) as conn:
            cursor = conn.execute(
                """
                INSERT INTO target_attempts (
                    run_id, target_type, target_id, subject, address, owner_id, phase, started_at, updated_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    target_type,
                    target_id,
                    subject,
                    address,
                    owner_id,
                    phase,
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                    error,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("INSERT into target_attempts did not produce a rowid")
            return TargetAttemptRecord(
                id=cursor.lastrowid,
                run_id=run_id,
                target_type=target_type,
                target_id=target_id,
                subject=subject,
                address=address,
                owner_id=owner_id,
                phase=phase,
                started_at=timestamp,
                updated_at=timestamp,
                error=error,
            )

    def update_target_attempts(
        self,
        *,
        run_id: int,
        target_ids: set[str],
        phase: str,
        error: str | None = None,
        now: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if not target_ids:
            return
        timestamp = _utcnow(now)
        placeholders = ", ".join("?" for _ in target_ids)
        params: list[object] = [phase, timestamp.isoformat(), error, run_id, *sorted(target_ids)]
        with self._connection(connection) as conn:
            conn.execute(
                f"""
                UPDATE target_attempts
                SET phase = ?, updated_at = ?, error = ?
                WHERE run_id = ? AND target_id IN ({placeholders})
                """,
                params,
            )

    def update_target_attempt_ids(
        self,
        attempt_ids: set[int],
        *,
        phase: str,
        error: str | None = None,
        now: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if not attempt_ids:
            return
        timestamp = _utcnow(now)
        placeholders = ", ".join("?" for _ in attempt_ids)
        params: list[object] = [phase, timestamp.isoformat(), error, *sorted(attempt_ids)]
        with self._connection(connection) as conn:
            conn.execute(
                f"""
                UPDATE target_attempts
                SET phase = ?, updated_at = ?, error = ?
                WHERE id IN ({placeholders})
                """,
                params,
            )

    def expire_claim_reservations_for_attempts(
        self,
        attempts: list[TargetAttemptRecord],
        *,
        status: str = "expired",
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if not attempts:
            return
        with self._connection(connection) as conn:
            for attempt in attempts:
                conn.execute(
                    """
                    UPDATE claim_reservations
                    SET status = ?
                    WHERE target_type = ? AND subject = ? AND address = ? AND status = 'reserved'
                    """,
                    (status, attempt.target_type, attempt.subject, attempt.address),
                )

    def target_attempts(
        self,
        run_id: int | None = None,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[TargetAttemptRecord]:
        query = """
            SELECT id, run_id, target_type, target_id, subject, address, owner_id, phase, started_at, updated_at, error
            FROM target_attempts
        """
        params: tuple[int, ...] = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            params = (run_id,)
        query += " ORDER BY id"
        with self._connection(connection) as conn:
            rows = conn.execute(query, params).fetchall()
        return [_target_attempt_from_row(row) for row in rows]

    def unfinished_target_attempts(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[TargetAttemptRecord]:
        with self._connection(connection) as conn:
            rows = conn.execute(
                """
                SELECT id, run_id, target_type, target_id, subject, address, owner_id, phase,
                       started_at, updated_at, error
                FROM target_attempts
                WHERE phase IN ('attempting', 'rolling_back')
                ORDER BY id
                """
            ).fetchall()
        return [_target_attempt_from_row(row) for row in rows]

    def record_snapshot(
        self,
        *,
        run_id: int,
        path: Path | str,
        content_hash: bytes,
        size: int,
        hash_algo: str = "sha256",
        mtime_ns: int | None = None,
        format: str | None = None,
        spec_hash: bytes | None = None,
        captured_at: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> SnapshotRecord:
        captured = _utcnow(captured_at)
        path_text = str(_canonical_path(path))
        with self._connection(connection) as conn:
            cursor = conn.execute(
                """
                INSERT INTO file_snapshots (
                    run_id, path, captured_at, hash_algo, content_hash, size, mtime_ns, format, spec_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    path_text,
                    captured.isoformat(),
                    hash_algo,
                    content_hash,
                    size,
                    mtime_ns,
                    format,
                    spec_hash,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("INSERT into file_snapshots did not produce a rowid")
            snapshot_id = cursor.lastrowid
        return SnapshotRecord(
            id=snapshot_id,
            run_id=run_id,
            path=Path(path_text),
            captured_at=captured,
            hash_algo=hash_algo,
            content_hash=content_hash,
            size=size,
            mtime_ns=mtime_ns,
            format=format,
            spec_hash=spec_hash,
        )

    def record_checked(
        self,
        *,
        run_id: int,
        path: Path | str,
        content_hash: bytes,
        size: int,
        hash_algo: str = "sha256",
        mtime_ns: int | None = None,
        format: str | None = None,
        spec_hash: bytes | None = None,
        changed: bool = False,
        summary: str | None = None,
        details: str | None = None,
        captured_at: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> tuple[SnapshotRecord, EventRecord]:
        with self._connection(connection) as conn:
            snapshot = self.record_snapshot(
                run_id=run_id,
                path=path,
                content_hash=content_hash,
                size=size,
                hash_algo=hash_algo,
                mtime_ns=mtime_ns,
                format=format,
                spec_hash=spec_hash,
                captured_at=captured_at,
                connection=conn,
            )
            event = self.record_event(
                run_id=run_id,
                event_type="checked",
                path=path,
                changed=changed,
                summary=summary,
                details=details,
                created_at=captured_at,
                connection=conn,
            )
        return snapshot, event

    def record_checkpoint(
        self,
        *,
        run_id: int,
        path: Path | str,
        content_text: str,
        format: str | None = None,
        original_exists: bool,
        captured_at: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> CheckpointRecord:
        captured = _utcnow(captured_at)
        path_text = str(_canonical_path(path))
        with self._connection(connection) as conn:
            cursor = conn.execute(
                """
                INSERT INTO file_checkpoints (run_id, path, captured_at, format, content_text, original_exists)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    path_text,
                    captured.isoformat(),
                    format,
                    content_text,
                    int(original_exists),
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("INSERT into file_checkpoints did not produce a rowid")
            checkpoint_id = cursor.lastrowid
        return CheckpointRecord(
            id=checkpoint_id,
            run_id=run_id,
            path=Path(path_text),
            captured_at=captured,
            format=format,
            content_text=content_text,
            original_exists=original_exists,
        )

    def record_event(
        self,
        *,
        run_id: int,
        event_type: str,
        path: Path | str | None = None,
        changed: bool = False,
        summary: str | None = None,
        details: str | None = None,
        created_at: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> EventRecord:
        created = _utcnow(created_at)
        path_text = None if path is None else str(_canonical_path(path))
        with self._connection(connection) as conn:
            cursor = conn.execute(
                """
                INSERT INTO events (run_id, created_at, event_type, path, changed, summary, details)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    created.isoformat(),
                    event_type,
                    path_text,
                    int(changed),
                    summary,
                    details,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("INSERT into events did not produce a rowid")
            event_id = cursor.lastrowid
        return EventRecord(
            id=event_id,
            run_id=run_id,
            created_at=created,
            event_type=event_type,
            path=None if path_text is None else Path(path_text),
            changed=changed,
            summary=summary,
            details=details,
        )

    def latest_snapshot(
        self,
        path: Path | str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> SnapshotRecord | None:
        path_text = str(_canonical_path(path))
        with self._connection(connection) as conn:
            row = conn.execute(
                """
                SELECT id, run_id, path, captured_at, hash_algo, content_hash, size, mtime_ns, format, spec_hash
                FROM file_snapshots
                WHERE path = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (path_text,),
            ).fetchone()
        if row is None:
            return None
        return SnapshotRecord(
            id=row[0],
            run_id=row[1],
            path=Path(row[2]),
            captured_at=_parse_datetime(row[3]),
            hash_algo=row[4],
            content_hash=row[5],
            size=row[6],
            mtime_ns=row[7],
            format=row[8],
            spec_hash=row[9],
        )

    def latest_checkpoint(
        self,
        path: Path | str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> CheckpointRecord | None:
        path_text = str(_canonical_path(path))
        with self._connection(connection) as conn:
            row = conn.execute(
                """
                SELECT id, run_id, path, captured_at, format, content_text, original_exists
                FROM file_checkpoints
                WHERE path = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (path_text,),
            ).fetchone()
        if row is None:
            return None
        return CheckpointRecord(
            id=row[0],
            run_id=row[1],
            path=Path(row[2]),
            captured_at=_parse_datetime(row[3]),
            format=row[4],
            content_text=row[5],
            original_exists=bool(row[6]),
        )

    def record_change_batch(
        self,
        *,
        run_id: int,
        path: Path | str,
        operations: list[dict[str, Any]],
        original_exists: bool,
        format: str | None = None,
        created_at: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> ChangeBatchRecord:
        created = _utcnow(created_at)
        path_text = str(_canonical_path(path))
        payload = json.dumps([_to_jsonable(operation) for operation in operations], ensure_ascii=False)
        with self._connection(connection) as conn:
            stored_original_exists = self.original_exists(path, connection=conn)
            if stored_original_exists is None:
                stored_original_exists = original_exists
            cursor = conn.execute(
                """
                INSERT INTO change_batches (run_id, path, created_at, format, original_exists, operations_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    path_text,
                    created.isoformat(),
                    format,
                    int(stored_original_exists),
                    payload,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("INSERT into change_batches did not produce a rowid")
            batch_id = cursor.lastrowid
        return ChangeBatchRecord(
            id=batch_id,
            run_id=run_id,
            path=Path(path_text),
            created_at=created,
            format=format,
            original_exists=stored_original_exists,
            operations=[_from_jsonable(operation) for operation in json.loads(payload)],
        )

    def latest_event(
        self,
        run_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> EventRecord | None:
        with self._connection(connection) as conn:
            row = conn.execute(
                """
                SELECT id, run_id, created_at, event_type, path, changed, summary, details
                FROM events
                WHERE run_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return EventRecord(
            id=row[0],
            run_id=row[1],
            created_at=_parse_datetime(row[2]),
            event_type=row[3],
            path=None if row[4] is None else Path(row[4]),
            changed=bool(row[5]),
            summary=row[6],
            details=row[7],
        )

    def change_batches(
        self,
        path: Path | str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[ChangeBatchRecord]:
        path_text = str(_canonical_path(path))
        with self._connection(connection) as conn:
            rows = conn.execute(
                """
                SELECT id, run_id, path, created_at, format, original_exists, operations_json
                FROM change_batches
                WHERE path = ?
                ORDER BY id ASC
                """,
                (path_text,),
            ).fetchall()
        return [
            ChangeBatchRecord(
                id=row[0],
                run_id=row[1],
                path=Path(row[2]),
                created_at=_parse_datetime(row[3]),
                format=row[4],
                original_exists=bool(row[5]),
                operations=[_from_jsonable(operation) for operation in json.loads(row[6])],
            )
            for row in rows
        ]

    def original_exists(
        self,
        path: Path | str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool | None:
        path_text = str(_canonical_path(path))
        with self._connection(connection) as conn:
            row = conn.execute(
                """
                SELECT original_exists
                FROM change_batches
                WHERE path = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (path_text,),
            ).fetchone()
        if row is None:
            return None
        return bool(row[0])

    def record_baseline(
        self,
        *,
        run_id: int,
        path: Path | str,
        content_text: str,
        format: str | None = None,
        original_exists: bool,
        captured_at: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> BaselineRecord:
        captured = _utcnow(captured_at)
        path_text = str(_canonical_path(path))
        with self._connection(connection) as conn:
            cursor = conn.execute(
                """
                INSERT INTO file_baselines (run_id, path, captured_at, format, content_text, original_exists)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    path_text,
                    captured.isoformat(),
                    format,
                    content_text,
                    int(original_exists),
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("INSERT into file_baselines did not produce a rowid")
            baseline_id = cursor.lastrowid
        return BaselineRecord(
            id=baseline_id,
            run_id=run_id,
            path=Path(path_text),
            captured_at=captured,
            format=format,
            content_text=content_text,
            original_exists=original_exists,
        )

    def original_baseline(
        self,
        path: Path | str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> BaselineRecord | None:
        path_text = str(_canonical_path(path))
        with self._connection(connection) as conn:
            row = conn.execute(
                """
                SELECT id, run_id, path, captured_at, format, content_text, original_exists
                FROM file_baselines
                WHERE path = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (path_text,),
            ).fetchone()
        if row is None:
            return None
        return BaselineRecord(
            id=row[0],
            run_id=row[1],
            path=Path(row[2]),
            captured_at=_parse_datetime(row[3]),
            format=row[4],
            content_text=row[5],
            original_exists=bool(row[6]),
        )

    def record_asset_backup(
        self,
        *,
        run_id: int,
        target_dest: Path | str,
        original_path: Path | str,
        backup_path: Path | str,
        content_hash: bytes,
        size: int,
        file_type: str,
        hash_algo: str = "sha256",
        mtime_ns: int | None = None,
        created_at: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> AssetBackupRecord:
        created = _utcnow(created_at)
        target_text = str(_canonical_path(target_dest))
        original_text = str(_canonical_path(original_path))
        backup_text = str(_canonical_path(backup_path))
        with self._connection(connection) as conn:
            cursor = conn.execute(
                """
                INSERT INTO asset_backups (
                    run_id, target_dest, original_path, backup_path, created_at, hash_algo,
                    content_hash, size, mtime_ns, file_type, restored_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    run_id,
                    target_text,
                    original_text,
                    backup_text,
                    created.isoformat(),
                    hash_algo,
                    content_hash,
                    size,
                    mtime_ns,
                    file_type,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("INSERT into asset_backups did not produce a rowid")
            backup_id = cursor.lastrowid
        return AssetBackupRecord(
            id=backup_id,
            run_id=run_id,
            target_dest=Path(target_text),
            original_path=Path(original_text),
            backup_path=Path(backup_text),
            created_at=created,
            hash_algo=hash_algo,
            content_hash=content_hash,
            size=size,
            mtime_ns=mtime_ns,
            file_type=file_type,
            restored_at=None,
        )

    def asset_backups(
        self,
        path: Path | str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[AssetBackupRecord]:
        path_text = str(_canonical_path(path))
        with self._connection(connection) as conn:
            rows = conn.execute(
                """
                SELECT id, run_id, target_dest, original_path, backup_path, created_at, hash_algo,
                       content_hash, size, mtime_ns, file_type, restored_at
                FROM asset_backups
                WHERE original_path = ?
                ORDER BY id ASC
                """,
                (path_text,),
            ).fetchall()
        return [_asset_backup_from_row(row) for row in rows]

    def unrestored_asset_backups_for_target(
        self,
        target_dest: Path | str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[AssetBackupRecord]:
        target_text = str(_canonical_path(target_dest))
        with self._connection(connection) as conn:
            rows = conn.execute(
                """
                SELECT id, run_id, target_dest, original_path, backup_path, created_at, hash_algo,
                       content_hash, size, mtime_ns, file_type, restored_at
                FROM asset_backups
                WHERE target_dest = ? AND restored_at IS NULL
                ORDER BY id ASC
                """,
                (target_text,),
            ).fetchall()
        return [_asset_backup_from_row(row) for row in rows]

    def mark_asset_backup_restored(
        self,
        backup_id: int,
        *,
        restored_at: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        restored = _utcnow(restored_at)
        with self._connection(connection) as conn:
            conn.execute("UPDATE asset_backups SET restored_at = ? WHERE id = ?", (restored.isoformat(), backup_id))

    def record_recovery_backup(
        self,
        *,
        run_id: int,
        target_path: Path | str,
        target_kind: str,
        operation: str,
        backup_path: Path | str,
        content_hash: bytes,
        size: int,
        hash_algo: str = "sha256",
        mtime_ns: int | None = None,
        content_text: str | None = None,
        created_at: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> RecoveryBackupRecord:
        created = _utcnow(created_at)
        target_text = str(_canonical_path(target_path))
        backup_text = str(_canonical_path(backup_path))
        with self._connection(connection) as conn:
            cursor = conn.execute(
                """
                INSERT INTO recovery_backups (
                    run_id, target_path, target_kind, operation, backup_path, created_at,
                    hash_algo, content_hash, size, mtime_ns, content_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    target_text,
                    target_kind,
                    operation,
                    backup_text,
                    created.isoformat(),
                    hash_algo,
                    content_hash,
                    size,
                    mtime_ns,
                    content_text,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("INSERT into recovery_backups did not produce a rowid")
            backup_id = cursor.lastrowid
        return RecoveryBackupRecord(
            id=backup_id,
            run_id=run_id,
            target_path=Path(target_text),
            target_kind=target_kind,
            operation=operation,
            backup_path=Path(backup_text),
            created_at=created,
            hash_algo=hash_algo,
            content_hash=content_hash,
            size=size,
            mtime_ns=mtime_ns,
            content_text=content_text,
        )

    def recovery_backups(
        self,
        path: Path | str | None = None,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[RecoveryBackupRecord]:
        query = """
            SELECT id, run_id, target_path, target_kind, operation, backup_path, created_at,
                   hash_algo, content_hash, size, mtime_ns, content_text
            FROM recovery_backups
        """
        params: tuple[str, ...] = ()
        if path is not None:
            query += " WHERE target_path = ?"
            params = (str(_canonical_path(path)),)
        query += " ORDER BY id ASC"
        with self._connection(connection) as conn:
            rows = conn.execute(query, params).fetchall()
        return [_recovery_backup_from_row(row) for row in rows]

    def latest_run_id(self, *, connection: sqlite3.Connection | None = None) -> int | None:
        with self._connection(connection) as conn:
            row = conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            return row[0] if row is not None else None

    def list_managed(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[ManagedRecord]:
        with self._connection(connection) as conn:
            rows = conn.execute(
                """
                SELECT path, format, MAX(created_at) AS last_applied_at
                FROM change_batches
                GROUP BY path
                ORDER BY path
                """
            ).fetchall()
        return [
            ManagedRecord(
                path=Path(row[0]),
                format=row[1],
                last_applied_at=_parse_datetime(row[2]),
            )
            for row in rows
        ]


def _canonical_path(path: Path | str) -> Path:
    return Path(path).resolve()


def _utcnow(value: datetime | None = None) -> datetime:
    if value is None:
        value = datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _to_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    return value


def _from_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _from_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_from_jsonable(item) for item in value]
    return value


def _asset_backup_from_row(row: sqlite3.Row | tuple[Any, ...]) -> AssetBackupRecord:
    return AssetBackupRecord(
        id=row[0],
        run_id=row[1],
        target_dest=Path(row[2]),
        original_path=Path(row[3]),
        backup_path=Path(row[4]),
        created_at=_parse_datetime(row[5]),
        hash_algo=row[6],
        content_hash=row[7],
        size=row[8],
        mtime_ns=row[9],
        file_type=row[10],
        restored_at=None if row[11] is None else _parse_datetime(row[11]),
    )


def _recovery_backup_from_row(row: sqlite3.Row | tuple[Any, ...]) -> RecoveryBackupRecord:
    return RecoveryBackupRecord(
        id=row[0],
        run_id=row[1],
        target_path=Path(row[2]),
        target_kind=row[3],
        operation=row[4],
        backup_path=Path(row[5]),
        created_at=_parse_datetime(row[6]),
        hash_algo=row[7],
        content_hash=row[8],
        size=row[9],
        mtime_ns=row[10],
        content_text=row[11],
    )


def _run_lock_record(row: RunLock) -> RunLockRecord:
    return RunLockRecord(
        name=str(row.name),
        owner=str(row.owner),
        token=None if row.token is None else str(row.token),
        run_id=None if row.run_id is None else int(row.run_id),
        acquired_at=_parse_datetime(str(row.acquired_at)),
        heartbeat_at=None if row.heartbeat_at is None else _parse_datetime(str(row.heartbeat_at)),
        expires_at=_parse_datetime(str(row.expires_at)),
    )


def _managed_claim_record(row: ManagedClaim) -> ManagedClaimRecord:
    return ManagedClaimRecord(
        id=int(row.id),
        target_type=str(row.target_type),
        subject=str(row.subject),
        address=str(row.address),
        owner_id=str(row.owner_id),
        spec_path=None if row.spec_path is None else str(row.spec_path),
        target_id=None if row.target_id is None else str(row.target_id),
        created_at=_parse_datetime(str(row.created_at)),
        last_seen_at=_parse_datetime(str(row.last_seen_at)),
    )


def _managed_claim_record_from_row(row: sqlite3.Row | tuple[Any, ...]) -> ManagedClaimRecord:
    return ManagedClaimRecord(
        id=row[0],
        target_type=row[1],
        subject=row[2],
        address=row[3],
        owner_id=row[4],
        spec_path=row[5],
        target_id=row[6],
        created_at=_parse_datetime(row[7]),
        last_seen_at=_parse_datetime(row[8]),
    )


def _claim_reservation_from_row(row: sqlite3.Row | tuple[Any, ...]) -> ClaimReservationRecord:
    return ClaimReservationRecord(
        id=row[0],
        target_type=row[1],
        subject=row[2],
        address=row[3],
        owner_id=row[4],
        run_id=row[5],
        token=row[6],
        status=row[7],
        created_at=_parse_datetime(row[8]),
        expires_at=_parse_datetime(row[9]),
    )


def _target_attempt_from_row(row: sqlite3.Row | tuple[Any, ...]) -> TargetAttemptRecord:
    return TargetAttemptRecord(
        id=row[0],
        run_id=row[1],
        target_type=row[2],
        target_id=row[3],
        subject=row[4],
        address=row[5],
        owner_id=row[6],
        phase=row[7],
        started_at=_parse_datetime(row[8]),
        updated_at=_parse_datetime(row[9]),
        error=row[10],
    )


def _spec_run_record(row: SpecRun) -> SpecRunRecord:
    return SpecRunRecord(
        id=int(row.id),
        run_id=int(row.run_id),
        spec_path=Path(str(row.spec_path)),
        spec_hash=None if row.spec_hash is None else bytes(row.spec_hash),
        order_index=int(row.order_index),
        valid=bool(row.valid),
    )


def _target_run_record(row: TargetRun) -> TargetRunRecord:
    return TargetRunRecord(
        id=int(row.id),
        run_id=int(row.run_id),
        spec_run_id=None if row.spec_run_id is None else int(row.spec_run_id),
        target_type=str(row.target_type),
        target_id=None if row.target_id is None else str(row.target_id),
        path_or_name=str(row.path_or_name),
        status=str(row.status),
        skip_reason=None if row.skip_reason is None else str(row.skip_reason),
        changed=bool(row.changed),
    )
