from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import cast

_NETWORK_FS_TYPES = {
    "9p",
    "afs",
    "cifs",
    "davfs",
    "fuse.sshfs",
    "ncpfs",
    "nfs",
    "nfs4",
    "smb3",
    "smbfs",
    "sshfs",
}


class NetworkStatePathError(ValueError):
    """Raised when the state database is placed on a network filesystem."""


def network_state_allowed() -> bool:
    value = os.environ.get("PRESCRIBE_ALLOW_NETWORK_STATE", "")
    return value.casefold() in {"1", "true", "yes", "on"}


def is_network_filesystem_path(path: Path | str) -> bool:
    if _is_special_sqlite_path(path):
        return False
    if sys.platform == "win32":
        return _is_windows_network_path(Path(path))
    return _is_posix_network_path(Path(path))


def _is_special_sqlite_path(path: Path | str) -> bool:
    raw = str(path)
    return raw == ":memory:" or raw.startswith("file:")


def _is_windows_network_path(path: Path) -> bool:
    if str(path).startswith(("\\\\", "//")):
        return True

    resolved = path.absolute()
    anchor = resolved.anchor
    if not anchor:
        return False

    drive_type = cast(int, ctypes.windll.kernel32.GetDriveTypeW(str(Path(anchor))))
    return drive_type == 4  # DRIVE_REMOTE


def _is_posix_network_path(path: Path) -> bool:
    mount = _nearest_existing_path(path)
    mounts = _linux_mounts()
    if not mounts:
        return False

    best_match = ""
    best_type = ""
    mount_text = str(mount)
    for mount_point, fs_type in mounts:
        matches_mount = mount_text == mount_point or mount_text.startswith(mount_point.rstrip("/") + "/")
        if matches_mount and len(mount_point) > len(best_match):
            best_match = mount_point
            best_type = fs_type

    return best_type.casefold() in _NETWORK_FS_TYPES


def _nearest_existing_path(path: Path) -> Path:
    current = path.expanduser().absolute()
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def _linux_mounts() -> list[tuple[str, str]]:
    mounts_path = Path("/proc/self/mounts")
    if not mounts_path.exists():
        mounts_path = Path("/proc/mounts")
    if not mounts_path.exists():
        return []

    mounts: list[tuple[str, str]] = []
    for line in mounts_path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 3:
            mounts.append((_unescape_mount_field(parts[1]), parts[2]))
    return mounts


def _unescape_mount_field(value: str) -> str:
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")
