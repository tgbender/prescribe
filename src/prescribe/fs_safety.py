from __future__ import annotations

import contextlib
import sys
from dataclasses import dataclass
from pathlib import Path


class UnsafePathError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PathSafety:
    path: Path
    exists: bool
    is_file: bool
    is_dir: bool
    is_symlink: bool
    is_mount: bool
    is_windows_reparse_point: bool
    is_windows_shortcut: bool
    hardlink_count: int
    size: int | None


def inspect_path(path: Path) -> PathSafety:
    is_symlink = path.is_symlink()
    exists = path.exists() or is_symlink
    is_mount = False
    with contextlib.suppress(OSError, NotImplementedError):
        is_mount = path.is_mount()
    is_reparse = _is_windows_reparse_point(path) if exists else False
    hardlink_count = 0
    size: int | None = None
    if exists and not is_symlink:
        with contextlib.suppress(OSError):
            stat = path.stat()
            hardlink_count = stat.st_nlink
            size = stat.st_size
    return PathSafety(
        path=path,
        exists=exists,
        is_file=path.is_file() and not is_symlink,
        is_dir=path.is_dir() and not is_symlink,
        is_symlink=is_symlink,
        is_mount=is_mount,
        is_windows_reparse_point=is_reparse,
        is_windows_shortcut=path.suffix.casefold() == ".lnk",
        hardlink_count=hardlink_count,
        size=size,
    )


def ensure_safe_managed_write_path(path: Path, *, operation: str) -> None:
    ensure_safe_parent_chain(path, operation=operation)
    safety = inspect_path(path)
    _raise_if_redirecting(safety, operation=operation)
    if safety.exists and not safety.is_file:
        raise UnsafePathError(f"{operation} refused for non-regular file: {path}")


def ensure_safe_asset_source_path(path: Path) -> None:
    ensure_safe_parent_chain(path, operation="asset source read")
    safety = inspect_path(path)
    _raise_if_redirecting(safety, operation="asset source read")
    if not safety.is_file:
        raise UnsafePathError(f"asset source read refused for non-regular file: {path}")


def ensure_safe_displace_regular_file(
    path: Path,
    *,
    operation: str,
    max_bytes: int,
    allow_binary: bool,
) -> None:
    ensure_safe_parent_chain(path, operation=operation)
    safety = inspect_path(path)
    _raise_if_redirecting(safety, operation=operation)
    if safety.is_dir:
        raise UnsafePathError(f"{operation} will not displace directories; clean up first: {path}")
    if not safety.is_file:
        raise UnsafePathError(f"{operation} refused for non-regular file: {path}")
    if safety.hardlink_count > 1:
        raise UnsafePathError(f"{operation} refused to displace hardlinked file: {path}")
    if safety.size is not None and safety.size > max_bytes:
        raise UnsafePathError(
            f"{operation} refused to displace {path}: size {safety.size} exceeds max_displace_bytes {max_bytes}"
        )
    if not allow_binary and _looks_binary(path):
        raise UnsafePathError(f"{operation} refused to displace binary-looking file: {path}")


def ensure_safe_unlink_path(path: Path, *, operation: str) -> None:
    ensure_safe_parent_chain(path, operation=operation)
    safety = inspect_path(path)
    _raise_if_redirecting(safety, operation=operation)
    if safety.exists and not safety.is_file:
        raise UnsafePathError(f"{operation} refused for non-regular file: {path}")


def ensure_safe_parent_chain(path: Path, *, operation: str) -> None:
    current = path.parent.absolute()
    anchor = Path(current.anchor)
    while True:
        if current == anchor or current == current.parent:
            return
        safety = inspect_path(current)
        if safety.exists and (safety.is_symlink or safety.is_windows_reparse_point):
            raise UnsafePathError(f"{operation} refused because parent path redirects elsewhere: {current}")
        current = current.parent


def _raise_if_redirecting(safety: PathSafety, *, operation: str) -> None:
    if safety.is_symlink:
        raise UnsafePathError(f"{operation} refused for symlink path: {safety.path}")
    if safety.is_windows_reparse_point:
        raise UnsafePathError(f"{operation} refused for Windows reparse point: {safety.path}")
    if safety.is_mount:
        raise UnsafePathError(f"{operation} refused for mount point: {safety.path}")


def _looks_binary(path: Path) -> bool:
    return b"\0" in path.read_bytes()[:4096]


def _is_windows_reparse_point(path: Path) -> bool:
    if sys.platform != "win32":
        return False
    try:
        from prescribe.platform.windows import is_reparse_point

        return is_reparse_point(path)
    except OSError:
        return False
