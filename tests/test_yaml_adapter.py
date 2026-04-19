from pathlib import Path

from prescribe.adapters.yaml import YamlAdapter


def test_yaml_round_trip_preserves_comments(make_text_file, yaml_sample: str, fake_root: Path) -> None:
    source = make_text_file("config.yaml", yaml_sample)

    adapter = YamlAdapter()
    document = adapter.load(source)

    assert document.format == "yaml"
    assert document.path == source
    assert document.root["title"] == "hello"
    assert document.root["tool"]["demo"]["value"] == 1

    target = fake_root / "output.yaml"
    adapter.dump(document, target)

    assert target.read_text() == source.read_text()
