from __future__ import annotations

from pathlib import Path

import pytest

from prescribe.adapters.jsonc import JsoncAdapter
from prescribe.jsonc import parse_jsonc
from prescribe.orchestrator import Orchestrator
from prescribe.spec import SpecError, SpecLoader


def test_jsonc_unknown_file_target_key_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "settings.jsonc"
    original = '{\n  "editor": {\n    "fontSize": 14\n  }\n}\n'
    config.write_text(original, encoding="utf-8")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'settings.jsonc'\n"
        "format = 'jsonc'\n"
        "[files.set]\n"
        '"editor.formatOnSave" = true\n',
        encoding="utf-8",
    )

    with pytest.raises(SpecError, match="unknown key.*set"):
        SpecLoader().load(spec_path)

    assert config.read_text(encoding="utf-8") == original


def test_jsonc_data_values_expand_spec_vars(state_store, tmp_path: Path) -> None:
    config = tmp_path / "settings.jsonc"
    config.write_text("{}\n", encoding="utf-8")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[vars]\n"
        "target = 'expanded-value'\n"
        "\n"
        "[[files]]\n"
        "path = 'settings.jsonc'\n"
        "format = 'jsonc'\n"
        "[files.data]\n"
        '"prescribe.dogfood.path" = "$target"\n',
        encoding="utf-8",
    )

    [target] = SpecLoader().load(spec_path).files
    assert target.data == {"prescribe.dogfood.path": "expanded-value"}

    [result] = Orchestrator(state_store).run(spec_path)

    assert result.status == "applied"
    assert parse_jsonc(config.read_text(encoding="utf-8"))["prescribe"]["dogfood"]["path"] == "expanded-value"


def test_jsonc_nested_object_insertion_is_indented(state_store, tmp_path: Path) -> None:
    config = tmp_path / "settings.jsonc"
    config.write_text(
        "{\n"
        "  // keep root\n"
        '  "editor": {\n'
        '    "fontSize": 14\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'settings.jsonc'\n"
        "format = 'jsonc'\n"
        "[files.data]\n"
        '"prescribe.dogfood.enabled" = true\n',
        encoding="utf-8",
    )

    [result] = Orchestrator(state_store).run(spec_path)

    text = config.read_text(encoding="utf-8")
    assert result.status == "applied"
    assert parse_jsonc(text)["prescribe"]["dogfood"]["enabled"] is True
    assert '  "prescribe": {\n    "dogfood": {' in text


def test_jsonc_rollback_prunes_empty_created_parent_objects(state_store, tmp_path: Path) -> None:
    config = tmp_path / "settings.jsonc"
    config.write_text(
        "{\n"
        '  "editor": {\n'
        '    "fontSize": 14\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'settings.jsonc'\n"
        "format = 'jsonc'\n"
        "[files.data]\n"
        '"prescribe.dogfood.enabled" = true\n'
        '"prescribe.dogfood.path" = "literal"\n',
        encoding="utf-8",
    )

    orchestrator = Orchestrator(state_store)
    [result] = orchestrator.run(spec_path)
    rolled_back = orchestrator.rollback(config)

    assert result.status == "applied"
    assert rolled_back.status == "rolled-back"
    assert JsoncAdapter().load(config).root == {"editor": {"fontSize": 14}}


def test_jsonc_rollback_keeps_empty_parent_objects_that_already_existed(state_store, tmp_path: Path) -> None:
    config = tmp_path / "settings.jsonc"
    config.write_text(
        "{\n"
        '  "prescribe": {\n'
        '    "dogfood": {}\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'settings.jsonc'\n"
        "format = 'jsonc'\n"
        "[files.data]\n"
        '"prescribe.dogfood.enabled" = true\n',
        encoding="utf-8",
    )

    orchestrator = Orchestrator(state_store)
    [result] = orchestrator.run(spec_path)
    rolled_back = orchestrator.rollback(config)

    assert result.status == "applied"
    assert rolled_back.status == "rolled-back"
    assert JsoncAdapter().load(config).root == {"prescribe": {"dogfood": {}}}
