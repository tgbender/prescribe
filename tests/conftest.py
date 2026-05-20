from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

from prescribe.state import StateStore


@pytest.fixture(autouse=True)
def _portable_home(monkeypatch) -> None:
    if "HOME" not in os.environ:
        fallback = os.environ.get("USERPROFILE") or str(Path.home())
        monkeypatch.setenv("HOME", fallback)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--file-db",
        action="store_true",
        default=False,
        help="Use real file-backed SQLite databases instead of :memory:",
    )
    parser.addoption(
        "--no-cli",
        action="store_true",
        default=False,
        help="Skip CLI integration tests even when `prescribe` is on PATH.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "cli: CLI integration tests — run by default when `prescribe` is on PATH, skip with --no-cli.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items):
    # --file-db: skip pyfakefs tests that are incompatible with real SQLite
    if config.getoption("--file-db"):
        skip = pytest.mark.skip(reason="pyfakefs incompatible with --file-db real SQLite")
        for item in items:
            if "state_store" in item.fixturenames:  # noqa
                if (
                    "fs" in item.fixturenames
                    or "make_text_file" in item.fixturenames
                    or "fake_root" in item.fixturenames
                ):  # noqa
                    item.add_marker(skip)

    # CLI tests: run by default when binary is available; skip on --no-cli or missing binary
    no_cli = config.getoption("--no-cli")
    cli_binary = shutil.which("prescribe")
    for item in items:
        if item.get_closest_marker("cli") is None:
            continue
        if no_cli:
            item.add_marker(pytest.mark.skip(reason="--no-cli"))
        elif cli_binary is None:
            item.add_marker(pytest.mark.skip(reason="prescribe not on PATH"))


TOML_SAMPLE = '# top comment\ntitle = "hello"\n[tool.demo]\n# keep this\nvalue = 1\n'

YAML_SAMPLE = "# top comment\ntitle: hello\ntool:\n  demo:\n    # keep this\n    value: 1\n"

LINE_SAMPLE = "# header\nunmanaged before\n# prescribe:begin managed\nold=1\n# prescribe:end managed\nunmanaged after\n"


@pytest.fixture()
def fake_root(fs) -> Path:
    root = Path("/workspace")
    fs.create_dir(root)
    return root


@pytest.fixture()
def make_text_file(fs, fake_root: Path) -> Callable[[str, str], Path]:
    def writer(name: str, content: str) -> Path:
        path = fake_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    return writer


@pytest.fixture(scope="session")
def _file_state_store_session(tmp_path_factory) -> StateStore:
    db_path = tmp_path_factory.getbasetemp() / "file_db" / "state.db"
    store = StateStore(db_path)
    store.initialize()
    return store


@pytest.fixture()
def file_state_store(_file_state_store_session) -> StateStore:
    return _file_state_store_session


@pytest.fixture()
def state_store(request) -> StateStore:
    """StateStore backed by :memory: (default) or a real file (--file-db)."""
    if request.config.getoption("--file-db"):
        return request.getfixturevalue("file_state_store")
    return request.getfixturevalue("memory_state_store")


@pytest.fixture()
def memory_state_store() -> StateStore:
    uri = f"file:prescribe-{uuid.uuid4().hex}?mode=memory&cache=shared"
    connection = sqlite3.connect(uri, uri=True)
    store = StateStore(uri, connection_factory=lambda _: sqlite3.connect(uri, uri=True))
    store.initialize()
    try:
        yield store
    finally:
        connection.close()


@pytest.fixture()
def toml_sample() -> str:
    return TOML_SAMPLE


@pytest.fixture()
def yaml_sample() -> str:
    return YAML_SAMPLE


@pytest.fixture()
def line_sample() -> str:
    return LINE_SAMPLE
