from pathlib import Path

from config_helper.adapters.json5 import Json5Adapter


def test_json5_round_trip(make_text_file, json5_sample: str, fake_root: Path) -> None:
    source = make_text_file("config.json5", json5_sample)

    adapter = Json5Adapter()
    document = adapter.load(source)

    assert document.format == "json5"
    assert document.path == source
    assert document.root["title"] == "hello"
    assert document.root["nested"]["value"] == 1

    target = fake_root / "output.json5"
    adapter.dump(document, target)

    assert adapter.load(target).root == document.root
    assert target.read_text().startswith("{\n")
    assert '\n  "title": "hello"' in target.read_text()
    assert "// top comment" not in target.read_text()
