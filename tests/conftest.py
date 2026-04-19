from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

import pytest

from prescribe.state import StateStore


TOML_SAMPLE = '# top comment\ntitle = "hello"\n[tool.demo]\n# keep this\nvalue = 1\n'

YAML_SAMPLE = (
    "# top comment\ntitle: hello\ntool:\n  demo:\n    # keep this\n    value: 1\n"
)

JSON5_SAMPLE = "// top comment\n{\n  title: 'hello',\n  nested: { value: 1, },\n}\n"

LINE_SAMPLE = (
    "# header\n"
    "unmanaged before\n"
    "# prescribe:begin managed\n"
    "old=1\n"
    "# prescribe:end managed\n"
    "unmanaged after\n"
)


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


@pytest.fixture()
def memory_state_store() -> StateStore:
    uri = "file::memory:?cache=shared"
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
def json5_sample() -> str:
    return JSON5_SAMPLE


@pytest.fixture()
def line_sample() -> str:
    return LINE_SAMPLE
