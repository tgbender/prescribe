from pathlib import Path

from config_helper.atomic import atomic_write_text


def test_atomic_write_text_replaces_file(tmp_path: Path) -> None:
    target = tmp_path / "config.txt"
    target.write_text("old\n")

    atomic_write_text(target, "new\n")

    assert target.read_text() == "new\n"
