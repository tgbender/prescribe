from __future__ import annotations

from pathlib import Path

import pytest

from prescribe.platform.windows import WindowsApi, is_remote_drive, replace_file_preserving_metadata


class FakeKernel32:
    def __init__(self, *, drive_type: int = 3, replace_ok: bool = True) -> None:
        self.drive_type = drive_type
        self.replace_ok = replace_ok
        self.drive_type_calls: list[str] = []
        self.replace_calls: list[tuple[str, str, str | None, int, object | None, object | None]] = []

    def GetDriveTypeW(self, root_path_name: str) -> int:
        self.drive_type_calls.append(root_path_name)
        return self.drive_type

    def ReplaceFileW(
        self,
        replaced_file_name: str,
        replacement_file_name: str,
        backup_file_name: str | None,
        replace_flags: int,
        exclude: object | None,
        reserved: object | None,
    ) -> int:
        self.replace_calls.append(
            (replaced_file_name, replacement_file_name, backup_file_name, replace_flags, exclude, reserved)
        )
        return 1 if self.replace_ok else 0


def test_windows_api_remote_drive_detection_uses_kernel32() -> None:
    kernel32 = FakeKernel32(drive_type=4)
    api = WindowsApi(kernel32=kernel32)

    assert is_remote_drive("Z:\\", api=api) is True
    assert kernel32.drive_type_calls == ["Z:\\"]


def test_windows_api_replace_file_argument_order() -> None:
    kernel32 = FakeKernel32()
    api = WindowsApi(kernel32=kernel32)

    replace_file_preserving_metadata(Path("source.tmp"), Path("dest.txt"), api=api)

    assert kernel32.replace_calls == [("dest.txt", "source.tmp", None, 2, None, None)]


def test_windows_api_replace_file_raises_oserror_on_failure() -> None:
    api = WindowsApi(kernel32=FakeKernel32(replace_ok=False))

    with pytest.raises(OSError):
        replace_file_preserving_metadata(Path("source.tmp"), Path("dest.txt"), api=api)
