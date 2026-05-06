from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

from prescribe.state import NetworkStatePathError, StateStore
from prescribe.state.filesystem import is_network_filesystem_path


def test_state_store_records_checked_snapshot_and_event(make_text_file, state_store) -> None:
    store = state_store

    source = make_text_file("config.toml", "title = 'hello'\n")
    digest = hashlib.sha256(source.read_bytes()).digest()

    run = store.start_run(spec_hash=b"spec", tool_version="1.0", host="test-host", platform="linux")
    snapshot, event = store.record_checked(
        run_id=run.id,
        path=source,
        content_hash=digest,
        size=source.stat().st_size,
        mtime_ns=source.stat().st_mtime_ns,
        format="toml",
        spec_hash=b"spec",
        summary="no changes",
    )

    assert snapshot.path == source.resolve()
    assert snapshot.content_hash == digest
    assert snapshot.hash_algo == "sha256"
    assert event.event_type == "checked"
    assert event.changed is False

    latest = store.latest_snapshot(source)
    assert latest is not None
    assert latest.content_hash == digest
    assert latest.size == source.stat().st_size


def test_state_store_allows_injected_connection_factory() -> None:
    seen_paths: list[Path] = []
    connection = sqlite3.connect(":memory:")

    def factory(path: Path) -> sqlite3.Connection:
        seen_paths.append(path)
        return connection

    store = StateStore(":memory:", connection_factory=factory)
    store.initialize()

    assert seen_paths == [Path(":memory:")]
    connection.close()


def test_state_store_persists_raw_hash_bytes(make_text_file, state_store) -> None:
    store = state_store

    source = make_text_file("config.yaml", "key: value\n")
    digest = hashlib.sha256(source.read_bytes()).digest()

    run = store.start_run()
    store.record_snapshot(
        run_id=run.id,
        path=source,
        content_hash=digest,
        size=source.stat().st_size,
        mtime_ns=source.stat().st_mtime_ns,
    )

    with store.connect() as connection:
        stored = connection.execute(
            "SELECT content_hash FROM file_snapshots WHERE path = ?",
            (str(source.resolve()),),
        ).fetchone()[0]

    assert stored == digest


def test_state_store_enables_wal_mode_for_file_db(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    store.initialize()
    with store.connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_state_store_rejects_network_state_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("prescribe.state.sqlite.is_network_filesystem_path", lambda path: True)

    with pytest.raises(NetworkStatePathError, match="network filesystem"):
        StateStore(tmp_path / "state.db")


def test_state_store_allows_network_state_path_when_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("prescribe.state.sqlite.is_network_filesystem_path", lambda path: True)

    store = StateStore(tmp_path / "state.db", allow_network_state=True)

    assert store.path == (tmp_path / "state.db").resolve()


def test_state_store_allows_network_state_path_with_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("prescribe.state.sqlite.is_network_filesystem_path", lambda path: True)
    monkeypatch.setenv("PRESCRIBE_ALLOW_NETWORK_STATE", "1")

    store = StateStore(tmp_path / "state.db")

    assert store.path == (tmp_path / "state.db").resolve()


def test_windows_unc_state_paths_are_network_filesystems(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "win32")

    assert is_network_filesystem_path(r"\\server\share\prescribe\state.db") is True


def test_state_store_transaction_commits_on_success(state_store) -> None:
    store = state_store
    with store.transaction() as conn:
        run = store.start_run(connection=conn)
    assert store.latest_event(run.id) is None


def test_state_store_transaction_rolls_back_on_error(state_store) -> None:
    store = state_store
    with store.transaction() as conn:
        store.start_run(connection=conn)
        store.record_event(run_id=1, event_type="test", path="/tmp/x", connection=conn)
    assert store.latest_event(1) is not None

    with pytest.raises(RuntimeError), store.transaction() as conn:
        store.start_run(connection=conn)
        store.record_event(run_id=2, event_type="test", path="/tmp/y", connection=conn)
        raise RuntimeError("boom")

    assert store.latest_event(2) is None


@pytest.mark.parametrize("use_file", [False, True])
def test_state_store_closes_connections_after_standalone_calls(use_file: bool, tmp_path: Path) -> None:
    opened = 0
    closed = 0

    class TrackingConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            nonlocal opened
            opened += 1

        def close(self):
            nonlocal closed
            closed += 1
            super().close()

    if use_file:
        db_path = str(tmp_path / "tracking.db")
        holder = None

        def factory(path):
            return TrackingConnection(db_path)
    else:
        db_path = "file::memory:?cache=shared"
        holder = sqlite3.connect(db_path, uri=True)

        def factory(path):
            return TrackingConnection(db_path, uri=True)

    store = StateStore(db_path, connection_factory=factory)
    store.initialize()
    store.start_run()
    store.start_run()

    assert opened > 0
    assert closed == opened, f"{closed} closed but {opened} opened"

    if holder is not None:
        holder.close()


def test_state_store_normalizes_paths_for_batches_and_original_exists(make_text_file, state_store) -> None:
    store = state_store

    source = make_text_file("config.toml", "title = 'hello'\n")

    run = store.start_run()
    batch = store.record_change_batch(
        run_id=run.id,
        path=source,
        operations=[{"kind": "set", "key": "count", "value": 1}],
        original_exists=True,
        format="toml",
    )

    snapshot = store.record_snapshot(
        run_id=run.id,
        path=source,
        content_hash=hashlib.sha256(source.read_bytes()).digest(),
        size=source.stat().st_size,
        format="toml",
    )

    alias = source
    if sys.platform == "darwin" and str(source).startswith("/private/"):
        alias = Path(str(source).replace("/private", "", 1))
        assert alias != source

    batches = store.change_batches(alias)
    assert len(batches) == 1
    assert batches[0].id == batch.id
    assert batches[0].path == source.resolve()
    assert store.original_exists(alias) is True
    assert store.latest_snapshot(alias) is not None
    assert store.latest_snapshot(alias).id == snapshot.id


def test_state_store_rejects_event_with_nonexistent_run_id(state_store) -> None:
    store = state_store
    with store.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO events "
            "(run_id, created_at, event_type, path, changed, summary, details) "
            "VALUES (?, datetime('now'), 'test', '/tmp/x', 0, NULL, NULL)",
            (999,),
        )
