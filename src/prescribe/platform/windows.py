from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

DRIVE_REMOTE = 4
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
REPLACEFILE_IGNORE_MERGE_ERRORS = 0x00000002


class _Kernel32(Protocol):
    def GetDriveTypeW(self, root_path_name: str) -> int: ...

    def GetFileAttributesW(self, file_name: str) -> int: ...

    def ReplaceFileW(
        self,
        replaced_file_name: str,
        replacement_file_name: str,
        backup_file_name: str | None,
        replace_flags: int,
        exclude: object | None,
        reserved: object | None,
    ) -> int: ...


@dataclass(frozen=True, slots=True)
class WindowsApi:
    kernel32: _Kernel32

    @classmethod
    def load(cls) -> WindowsApi:
        if sys.platform != "win32":
            raise RuntimeError("WindowsApi is only available on Windows")
        return cls(kernel32=cast(_Kernel32, ctypes.WinDLL("kernel32", use_last_error=True)))

    def is_remote_drive(self, anchor: str) -> bool:
        return self.kernel32.GetDriveTypeW(anchor) == DRIVE_REMOTE

    def is_reparse_point(self, path: Path) -> bool:
        attrs = self.kernel32.GetFileAttributesW(str(path))
        if attrs == -1:
            return False
        return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)

    def replace_file(self, source: Path, dest: Path) -> None:
        ok = self.kernel32.ReplaceFileW(
            str(dest),
            str(source),
            None,
            REPLACEFILE_IGNORE_MERGE_ERRORS,
            None,
            None,
        )
        if not ok:
            get_last_error = getattr(ctypes, "get_last_error", None)
            error = get_last_error() if get_last_error is not None else 0
            format_error = getattr(ctypes, "FormatError", None)
            message = format_error(error) if error and format_error is not None else "ReplaceFileW failed"
            raise OSError(error, message, str(dest))


def default_api() -> WindowsApi:
    return WindowsApi.load()


def is_remote_drive(anchor: str, *, api: WindowsApi | None = None) -> bool:
    return (api or default_api()).is_remote_drive(anchor)


def is_reparse_point(path: Path, *, api: WindowsApi | None = None) -> bool:
    return (api or default_api()).is_reparse_point(path)


def replace_file_preserving_metadata(source: Path, dest: Path, *, api: WindowsApi | None = None) -> None:
    (api or default_api()).replace_file(source, dest)
