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
