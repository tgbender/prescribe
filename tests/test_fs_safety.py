from __future__ import annotations

from pathlib import Path

import pytest

from prescribe.fs_safety import (
    UnsafePathError,
    ensure_safe_displace_regular_file,
    ensure_safe_managed_write_path,
    inspect_path,
)


def test_shortcut_extension_is_classified_as_regular_file(tmp_path: Path) -> None:
    shortcut = tmp_path / "Tool.lnk"
    shortcut.write_bytes(b"not parsed by prescribe")

    safety = inspect_path(shortcut)

    assert safety.is_windows_shortcut is True
    assert safety.is_file is True


def test_managed_write_rejects_symlink_target(tmp_path: Path) -> None:
    real = tmp_path / "real.txt"
    real.write_text("real\n")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(UnsafePathError, match="symlink"):
        ensure_safe_managed_write_path(link, operation="file target")


def test_managed_write_rejects_symlink_parent(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    try:
        link_dir.symlink_to(real_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(UnsafePathError, match="parent path redirects"):
        ensure_safe_managed_write_path(link_dir / "config.toml", operation="file target")


def test_displace_rejects_directories(tmp_path: Path) -> None:
    directory = tmp_path / "extra"
    directory.mkdir()

    with pytest.raises(UnsafePathError, match="will not displace directories"):
        ensure_safe_displace_regular_file(
            directory,
            operation="asset replace",
            max_bytes=1024,
            allow_binary=True,
        )


def test_displace_rejects_hardlinked_file(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source\n")
    link = tmp_path / "link.txt"
    try:
        link.hardlink_to(source)
    except OSError as exc:
        pytest.skip(f"hardlink creation unavailable: {exc}")

    with pytest.raises(UnsafePathError, match="hardlinked"):
        ensure_safe_displace_regular_file(
            link,
            operation="asset replace",
            max_bytes=1024,
            allow_binary=True,
        )
