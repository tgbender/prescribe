import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str, *, newline: str | None = None) -> None:
    """Write content to path atomically via temp file + replace.

    Args:
        newline: Controls line ending translation.
            None (default): platform default (translates \\n on Windows in text mode).
            "": write in binary mode — no translation, preserves exact line endings.
            "\\n" or "\\r\\n": Python's ``open(newline=...)`` behavior.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if newline == "":
        data = content.encode("utf-8")
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(data)
            handle.flush()
            temp_path = Path(handle.name)
    else:
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
    temp_path.replace(path)


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
