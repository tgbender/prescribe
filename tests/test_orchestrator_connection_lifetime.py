import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from prescribe import FileTarget, Orchestrator, Spec, StateStore, toml_adapter


class TrackedConnection(sqlite3.Connection):
    closed = False

    def close(self) -> None:
        super().close()
        self.closed = True


class FailingReadStore(StateStore):
    fail_reads = False

    def change_batches(self, path, *, connection=None):
        if self.fail_reads:
            raise RuntimeError("injected managed-state read failure")
        return super().change_batches(path, connection=connection)


@pytest.fixture
def tracked_store(tmp_path):
    connections = []

    def connect(path):
        connection = sqlite3.connect(path, factory=TrackedConnection)
        connections.append(connection)
        return connection

    store = FailingReadStore(tmp_path / "state.sqlite", connection_factory=connect)
    try:
        yield store, connections
    finally:
        for connection in connections:
            if not connection.closed:
                connection.close()


@pytest.mark.parametrize("outcome", ["applied", "noop", "conflict", "error"])
def test_existing_file_apply_closes_owned_connections(tmp_path, tracked_store, outcome):
    store, connections = tracked_store
    target = tmp_path / "app.toml"
    target.write_text('theme = "light"\n', encoding="utf-8")
    spec = Spec(path=tmp_path / "spec.toml", files=[FileTarget(target, "toml", data={"theme": "dark"})])
    orchestrator = Orchestrator(store)
    if outcome in {"noop", "conflict"}:
        assert orchestrator.run(spec)[0].status == "applied"
    if outcome == "conflict":
        target.write_text('theme = "blue"\n', encoding="utf-8")
    store.fail_reads = outcome == "error"
    first_new_connection = len(connections)

    result = orchestrator.run(spec)

    assert any(r.status == outcome for r in result), result
    expected = "blue" if outcome == "conflict" else "light" if outcome == "error" else "dark"
    assert target.read_text(encoding="utf-8") == f'theme = "{expected}"\n'
    opened = connections[first_new_connection:]
    assert opened
    assert all(connection.closed for connection in opened), "apply leaked an owned SQLite connection"


@pytest.mark.parametrize("fail_read", [False, True])
def test_existing_file_check_preserves_borrowed_connection_and_transaction(tmp_path, tracked_store, fail_read):
    store, _ = tracked_store
    store.initialize()
    run = store.start_run()
    target = tmp_path / "app.toml"
    target.write_text('theme = "light"\n', encoding="utf-8")
    file_target = FileTarget(target, "toml", data={"theme": "light"})
    connection = store.connect()
    try:
        connection.execute("CREATE TABLE caller_data (value TEXT)")
        connection.execute("INSERT INTO caller_data VALUES ('uncommitted')")
        store.fail_reads = fail_read

        def check():
            return Orchestrator(store)._process_existing_file(
                run.id, b"spec", file_target, toml_adapter, connection=connection
            )

        if fail_read:
            with pytest.raises(RuntimeError, match="injected managed-state read failure"):
                check()
        else:
            assert check().status == "noop"
        assert not connection.closed
        assert connection.in_transaction
        assert connection.execute("SELECT value FROM caller_data").fetchall() == [("uncommitted",)]
        connection.rollback()
        assert connection.execute("SELECT value FROM caller_data").fetchall() == []
    finally:
        connection.close()


def test_apply_and_rollback_allow_immediate_state_directory_cleanup(tmp_path):
    with TemporaryDirectory(dir=tmp_path) as directory:
        root = Path(directory)
        target = root / "app.toml"
        original = 'theme = "light"\n'
        target.write_text(original, encoding="utf-8")
        spec = Spec(path=root / "spec.toml", files=[FileTarget(target, "toml", data={"theme": "dark"})])
        orchestrator = Orchestrator(StateStore(root / "state.sqlite"))
        assert orchestrator.run(spec)[0].status == "applied"
        assert orchestrator.run(spec)[0].status == "noop"
        assert orchestrator.rollback(target).status == "rolled-back"
        assert target.read_text(encoding="utf-8") == original
    assert not root.exists()
