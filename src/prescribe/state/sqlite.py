from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RunRecord:
    id: int
    started_at: datetime
    spec_hash: bytes | None
    tool_version: str | None
    host: str | None
    platform: str | None


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


class StateStore:
    def __init__(
        self,
        path: Path | str,
        *,
        connection_factory: Callable[[Path], sqlite3.Connection] = sqlite3.connect,
    ) -> None:
        raw_path = Path(path)
        self.path = raw_path if str(raw_path) == ":memory:" else _canonical_path(raw_path)
        self.connection_factory = connection_factory

    def _open(self) -> sqlite3.Connection:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
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

    def initialize(self, *, connection: sqlite3.Connection | None = None) -> None:
        with self._connection(connection) as conn:
            conn.executescript(_SCHEMA)

    def start_run(
        self,
        *,
        spec_hash: bytes | None = None,
        tool_version: str | None = None,
        host: str | None = None,
        platform: str | None = None,
        started_at: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> RunRecord:
        started = _utcnow(started_at)
        with self._connection(connection) as conn:
            cursor = conn.execute(
                """
                INSERT INTO runs (started_at, spec_hash, tool_version, host, platform)
                VALUES (?, ?, ?, ?, ?)
                """,
                (started.isoformat(), spec_hash, tool_version, host, platform),
            )
            assert cursor.lastrowid is not None
            run_id = cursor.lastrowid
        return RunRecord(
            id=run_id,
            started_at=started,
            spec_hash=spec_hash,
            tool_version=tool_version,
            host=host,
            platform=platform,
        )

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
            assert cursor.lastrowid is not None
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
            assert cursor.lastrowid is not None
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
            assert cursor.lastrowid is not None
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
            assert cursor.lastrowid is not None
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
            assert cursor.lastrowid is not None
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

    def latest_baseline(
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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    spec_hash BLOB,
    tool_version TEXT,
    host TEXT,
    platform TEXT
);

CREATE TABLE IF NOT EXISTS file_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    hash_algo TEXT NOT NULL,
    content_hash BLOB NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER,
    format TEXT,
    spec_hash BLOB
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    path TEXT,
    changed INTEGER NOT NULL,
    summary TEXT,
    details TEXT
);

CREATE INDEX IF NOT EXISTS idx_file_snapshots_path_id
ON file_snapshots(path, id DESC);

CREATE INDEX IF NOT EXISTS idx_events_run_id_id
ON events(run_id, id DESC);

CREATE TABLE IF NOT EXISTS change_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    format TEXT,
    original_exists INTEGER NOT NULL,
    operations_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_change_batches_path_id
ON change_batches(path, id DESC);

CREATE TABLE IF NOT EXISTS file_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    format TEXT,
    content_text TEXT NOT NULL,
    original_exists INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_file_checkpoints_path_id
ON file_checkpoints(path, id DESC);

CREATE TABLE IF NOT EXISTS file_baselines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    format TEXT,
    content_text TEXT NOT NULL,
    original_exists INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_file_baselines_path_id
ON file_baselines(path, id ASC);
"""


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
