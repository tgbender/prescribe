from pathlib import Path

import pytest

from prescribe.adapters.json5 import Json5Adapter
from prescribe.adapters.jsonc import JsoncAdapter
from prescribe.adapters.line import LineAdapter
from prescribe.adapters.registry import adapter_for_path
from prescribe.adapters.toml import TomlAdapter
from prescribe.adapters.yaml import YamlAdapter


def test_adapter_registry_recognizes_supported_extensions() -> None:
    assert isinstance(adapter_for_path(Path("a.toml")), TomlAdapter)
    assert isinstance(adapter_for_path(Path("a.yaml")), YamlAdapter)
    assert isinstance(adapter_for_path(Path("a.yml")), YamlAdapter)
    assert isinstance(adapter_for_path(Path("a.json5")), Json5Adapter)
    assert isinstance(adapter_for_path(Path("a.json")), JsoncAdapter)
    assert isinstance(adapter_for_path(Path("a.jsonc")), JsoncAdapter)
    assert isinstance(adapter_for_path(Path("a.code-workspace")), JsoncAdapter)
    assert isinstance(adapter_for_path(Path(".env")), LineAdapter)
    assert isinstance(adapter_for_path(Path(".gitignore")), LineAdapter)
    assert isinstance(adapter_for_path(Path("settings.conf")), LineAdapter)


def test_adapter_registry_rejects_unknown_extensions() -> None:
    with pytest.raises(ValueError):
        adapter_for_path(Path("a.unknown"))
