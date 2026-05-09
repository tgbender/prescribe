"""Tests that migrate() preserves data across schema changes."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from prescribe.state import StateStore
from prescribe.state.migrate import migrate


def test_migrate_adds_column_without_data_loss() -> None:
    """Adding a column via ALTER TABLE should leave existing data intact."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL,
            spec_hash BLOB
        )
    """)
    conn.execute("INSERT INTO runs (id, started_at, spec_hash) VALUES (1, '2024-01-01', X'ABCD')")
    conn.commit()

    migrate(conn, dry_run=False)

    row = conn.execute("SELECT id, started_at, spec_hash FROM runs").fetchone()
    assert row == (1, "2024-01-01", b"\xab\xcd")

    conn.close()


def test_migrate_rebuild_copies_shared_columns() -> None:
    """When a table must be rebuilt, shared columns should be copied over.

    Previously: _rebuild archived the old table and recreated it empty,
    losing all data.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL,
            spec_hash BLOB
        )
    """)
    conn.execute("INSERT INTO runs (id, started_at, spec_hash) VALUES (42, '2024-06-15', X'DEAD')")
    conn.commit()

    # Add the columns the current schema expects, plus an extra legacy column
    conn.execute("ALTER TABLE runs ADD COLUMN tool_version TEXT")
    conn.execute("ALTER TABLE runs ADD COLUMN host TEXT")
    conn.execute("ALTER TABLE runs ADD COLUMN platform TEXT")
    conn.execute("ALTER TABLE runs ADD COLUMN legacy_col TEXT")
    conn.execute(
        "INSERT INTO runs (id, started_at, spec_hash, tool_version, legacy_col) "
        "VALUES (43, '2024-07-01', X'BEEF', '1.0', 'old')"
    )
    conn.commit()

    migrate(conn, dry_run=False)

    # After migration, runs should be rebuilt without legacy_col but WITH data
    rows = conn.execute("SELECT id, started_at, spec_hash, tool_version FROM runs ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0] == (42, "2024-06-15", b"\xde\xad", None)
    assert rows[1] == (43, "2024-07-01", b"\xbe\xef", "1.0")

    conn.close()


def test_migrate_rebuild_with_fk_parent_copied_before_child() -> None:
    """Rebuilding a table with FK children requires parent data to exist.

    Because _rebuild archives, recreates, then copies data back, the parent
    'runs' table must be copied before any child table referencing it.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = OFF")

    # Set up a minimal schema matching the current models (runs + file_snapshots)
    conn.execute("""
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL,
            spec_hash BLOB,
            tool_version TEXT,
            host TEXT,
            platform TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE file_snapshots (
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL,
            path TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            hash_algo TEXT NOT NULL,
            content_hash BLOB NOT NULL,
            size INTEGER NOT NULL,
            mtime_ns INTEGER,
            format TEXT,
            spec_hash BLOB
        )
    """)
    conn.execute("INSERT INTO runs (id, started_at) VALUES (1, '2024-01-01')")
    conn.execute(
        "INSERT INTO file_snapshots "
        "VALUES (1, 1, '/etc/config', '2024-01-01', 'sha256', X'ABCD', 42, 1234567890, 'toml', X'BEEF')"
    )
    conn.commit()

    # Force rebuild of file_snapshots by adding an extra column
    conn.execute("ALTER TABLE file_snapshots ADD COLUMN new_col TEXT")
    conn.commit()

    migrate(conn, dry_run=False)

    # Verify file_snapshots data was preserved
    row = conn.execute("SELECT id, run_id, path, hash_algo, content_hash, size, format FROM file_snapshots").fetchone()
    assert row is not None
    assert row[0] == 1
    assert row[1] == 1
    assert row[2] == "/etc/config"
    assert row[4] == b"\xab\xcd"
    assert row[5] == 42

    conn.close()


def test_state_store_migrates_03_database_to_recovery_schema(tmp_path: Path) -> None:
    """A 0.3-style state DB should gain 0.4 recovery tables/columns in place."""
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL,
            spec_hash BLOB,
            tool_version TEXT,
            host TEXT,
            platform TEXT,
            ended_at TEXT,
            status TEXT,
            command TEXT,
            cwd TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE run_locks (
            name TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
            acquired_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE managed_claims (
            id INTEGER PRIMARY KEY,
            target_type TEXT NOT NULL,
            subject TEXT NOT NULL,
            address TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            spec_path TEXT,
            target_id TEXT,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
    """)
    conn.execute("INSERT INTO runs (id, started_at, status) VALUES (1, '2026-05-01T00:00:00+00:00', 'applied')")
    conn.execute(
        "INSERT INTO run_locks (name, owner, run_id, acquired_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        (
            "global",
            "old-owner",
            1,
            "2026-05-01T00:00:00+00:00",
            "2026-05-01T00:01:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO managed_claims (
            id, target_type, subject, address, owner_id, spec_path, target_id, created_at, last_seen_at
        )
        VALUES (10, 'file', 'config.toml', 'count', 'spec.toml#files[0]', 'spec.toml', 'files[0]',
                '2026-05-01T00:00:00+00:00', '2026-05-01T00:00:00+00:00')
        """
    )
    conn.commit()
    conn.close()

    store = StateStore(db_path)
    store.initialize()

    with store.connect() as migrated:
        lock_columns = {row[1] for row in migrated.execute("PRAGMA table_info('run_locks')").fetchall()}
        tables = {
            row[0]
            for row in migrated.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        old_lock = migrated.execute("SELECT owner, token, heartbeat_at FROM run_locks WHERE name = 'global'").fetchone()

    assert {"token", "heartbeat_at"}.issubset(lock_columns)
    assert {"claim_reservations", "target_attempts"}.issubset(tables)
    assert old_lock == ("old-owner", None, None)
    assert store.claims()[0].owner_id == "spec.toml#files[0]"
    assert store.claim_reservations() == []
    assert store.target_attempts() == []

    replacement = store.acquire_lock(
        "global",
        owner="new-owner",
        ttl=timedelta(minutes=1),
        now=datetime(2026, 5, 1, 0, 2, tzinfo=UTC),
    )
    assert replacement.owner == "new-owner"
    assert replacement.token is not None
