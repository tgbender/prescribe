import sys
import tempfile
from pathlib import Path
from stat import S_IMODE

from prescribe.platform.windows import replace_file_preserving_metadata


def atomic_write_text(path: Path, content: str, *, newline: str | None = None) -> None:
    """Write content to path atomically via temp file + replace.

    Args:
        newline: Controls line ending translation.
            None (default): platform default (translates \\n on Windows in text mode).
            "": write in binary mode — no translation, preserves exact line endings.
            "\\n" or "\\r\\n": Python's ``open(newline=...)`` behavior.
    """
    if newline == "":
        atomic_write_bytes(path, content.encode("utf-8"))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = _existing_permissions(path)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline=newline,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(content)
        handle.flush()
        temp_path = Path(handle.name)
    if existing_mode is not None:
        temp_path.chmod(existing_mode)
    _replace_file(temp_path, path)


def atomic_write_bytes(path: Path, content: bytes, *, permissions: int | None = None) -> None:
    """Write bytes atomically, preserving existing mode/ACLs when replacing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = _existing_permissions(path)
    mode = permissions if permissions is not None else existing_mode
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(content)
        handle.flush()
        temp_path = Path(handle.name)
    if mode is not None:
        temp_path.chmod(mode)
    _replace_file(temp_path, path)


def preferred_text_newline(path: Path, *, default: str = "\n") -> str:
    """Return the first newline convention found in a text file, or default."""
    if not path.exists():
        return default
    sample = path.read_bytes()
    crlf = sample.find(b"\r\n")
    lf = sample.find(b"\n")
    cr = sample.find(b"\r")
    positions = [(index, newline) for index, newline in [(crlf, "\r\n"), (lf, "\n"), (cr, "\r")] if index >= 0]
    if not positions:
        return default
    return min(positions, key=lambda item: item[0])[1]


def normalize_newlines(text: str, newline: str) -> str:
    """Normalize all newline spellings in text to newline."""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)


def permission_bits(path: Path) -> int:
    return S_IMODE(path.stat().st_mode)


def _existing_permissions(path: Path) -> int | None:
    if not path.exists() or path.is_symlink():
        return None
    return permission_bits(path)


def _replace_file(temp_path: Path, path: Path) -> None:
    if sys.platform == "win32" and path.exists() and not path.is_symlink():
        _replace_file_windows(temp_path, path)
        return
    temp_path.replace(path)


def _replace_file_windows(temp_path: Path, path: Path) -> None:
    existing_mode = _existing_permissions(path)
    if existing_mode is not None and existing_mode & 0o222 == 0:
        path.chmod(existing_mode | 0o200)
    try:
        replace_file_preserving_metadata(temp_path, path)
    except OSError:
        # pyfakefs and similar virtual filesystems are visible to Python but not
        # to the native Windows API. Fall back so tests still exercise write
        # behavior; real Windows paths use ReplaceFileW above.
        temp_path.replace(path)
    finally:
        if path.exists() and existing_mode is not None:
            path.chmod(existing_mode)
