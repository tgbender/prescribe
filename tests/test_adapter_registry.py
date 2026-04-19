from pathlib import Path

import pytest

from config_helper.adapters.registry import adapter_for_path
from config_helper.adapters.toml import TomlAdapter
from config_helper.adapters.yaml import YamlAdapter
from config_helper.adapters.json5 import Json5Adapter
from config_helper.adapters.jsonc import JsoncAdapter
from config_helper.adapters.line import LineAdapter


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
