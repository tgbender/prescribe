"""Tests that migrate() preserves data across schema changes."""

from __future__ import annotations

import sqlite3

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
