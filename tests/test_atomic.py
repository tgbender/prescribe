from pathlib import Path
from stat import S_IMODE

from prescribe.atomic import atomic_write_text


def test_atomic_write_text_replaces_file(tmp_path: Path) -> None:
    target = tmp_path / "config.txt"
    target.write_text("old\n")

    atomic_write_text(target, "new\n")

    assert target.read_text() == "new\n"


def test_atomic_write_text_preserves_readonly_bit_on_windows(tmp_path: Path) -> None:
    target = tmp_path / "readonly.txt"
    target.write_text("old\n")
    target.chmod(0o444)

    atomic_write_text(target, "new\n", newline="")

    assert target.read_text() == "new\n"
    assert S_IMODE(target.stat().st_mode) & 0o222 == 0
