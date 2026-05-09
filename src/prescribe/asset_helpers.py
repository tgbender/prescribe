import contextlib
import difflib
import glob
import os
from dataclasses import dataclass
from pathlib import Path

from prescribe.atomic import permission_bits
from prescribe.encoding import read_utf8_text
from prescribe.fs_safety import UnsafePathError, ensure_safe_displace_regular_file
from prescribe.spec import AssetTarget


@dataclass(slots=True)
class AssetDestinationState:
    exists: bool
    text: str | None = None
    is_symlink: bool = False
    symlink_target: str | None = None
    hardlink_count: int = 0
    permissions: int | None = None


def asset_entries(target: AssetTarget) -> list[tuple[Path, Path]]:
    if target.mode == "file":
        source = Path(target.source)
        if not source.exists() or not source.is_file():
            return []
        return [(source, target.dest)]

    matches = sorted(Path(match).resolve() for match in glob.glob(target.source, recursive=True))
    files = [match for match in matches if match.is_file()]
    base = asset_source_base(target.source)
    entries: list[tuple[Path, Path]] = []
    for source in files:
        rel = source.relative_to(base)
        entries.append((source, target.dest / rel))
    return entries


def asset_destination_state(path: Path) -> AssetDestinationState:
    is_symlink = path.is_symlink()
    exists = path.exists() or is_symlink
    if not exists:
        return AssetDestinationState(exists=False)
    symlink_target = os.readlink(path) if is_symlink else None
    text = read_utf8_text(path) if path.exists() else None
    hardlink_count = 0
    permissions: int | None = None
    if path.exists() and not is_symlink:
        with contextlib.suppress(OSError):
            hardlink_count = path.stat().st_nlink
            permissions = permission_bits(path)
    return AssetDestinationState(
        exists=True,
        text=text,
        is_symlink=is_symlink,
        symlink_target=symlink_target,
        hardlink_count=hardlink_count,
        permissions=permissions,
    )


def asset_extra_paths(target: AssetTarget, entries: list[tuple[Path, Path]]) -> list[Path]:
    if not target.dest.exists() or not target.dest.is_dir():
        return []
    desired = {dest.absolute() for _, dest in entries}
    extras: list[Path] = []
    for path in sorted(target.dest.rglob("*")):
        resolved = path.absolute()
        if resolved in desired:
            continue
        if path.is_dir() and any(is_relative_to(dest, resolved) for dest in desired):
            continue
        if path.is_symlink() or path.is_file() or path.is_dir():
            extras.append(path)
    return extras


def unsafe_asset_replace_reason(target: AssetTarget, extras: list[Path]) -> str | None:
    if not extras:
        return None
    if not safe_asset_replace_root(target.dest):
        return f"asset replace destination is too broad: {target.dest}"
    for path in extras:
        try:
            ensure_safe_displace_regular_file(
                path,
                operation="asset replace",
                max_bytes=target.max_displace_bytes,
                allow_binary=target.allow_binary,
            )
        except UnsafePathError as exc:
            return str(exc)
    return None


def safe_asset_replace_root(path: Path) -> bool:
    resolved = path.resolve()
    home = Path(os.environ.get("HOME") or Path.home()).resolve()
    if resolved == home:
        return False
    if resolved.parent == resolved:
        return False
    anchor = Path(resolved.anchor)
    return resolved != anchor


def asset_source_base(source_pattern: str) -> Path:
    parts = Path(source_pattern).parts
    base_parts: list[str] = []
    for part in parts:
        if any(char in part for char in "*?["):
            break
        base_parts.append(part)
    if not base_parts:
        return Path(".").resolve()
    base = Path(*base_parts)
    if base.is_file():
        return base.parent
    return base.resolve()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def asset_diff(dest: Path, current_text: str | None, desired_text: str) -> str:
    before = [] if current_text is None else current_text.splitlines(keepends=True)
    after = desired_text.splitlines(keepends=True)
    tofile_suffix = " (new)" if current_text is None else " (desired)"
    return "".join(
        difflib.unified_diff(
            before,
            after,
            fromfile=str(dest),
            tofile=str(dest) + tofile_suffix,
        )
    )


def asset_replace_diff(extras: list[Path]) -> str:
    lines = ["# asset replace would move extra files to backup:\n"]
    lines.extend(f"# - {path}\n" for path in extras)
    return "".join(lines)


def format_permissions(permissions: int | None) -> str | None:
    return None if permissions is None else f"{permissions:04o}"
