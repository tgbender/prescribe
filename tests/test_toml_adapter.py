from pathlib import Path

from prescribe.adapters.toml import TomlAdapter


def test_toml_round_trip_preserves_comments(
    make_text_file, toml_sample: str, fake_root: Path
) -> None:
    source = make_text_file("config.toml", toml_sample)

    adapter = TomlAdapter()
    document = adapter.load(source)

    assert document.format == "toml"
    assert document.path == source
    assert document.root["title"] == "hello"
    assert document.root["tool"]["demo"]["value"] == 1

    target = fake_root / "output.toml"
    adapter.dump(document, target)

    assert target.read_text() == source.read_text()
