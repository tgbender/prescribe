from __future__ import annotations

from pathlib import Path


class TextEncodingError(ValueError):
    """Raised when a managed text file is not UTF-8."""


def read_utf8_text(path: Path) -> str:
    return decode_utf8_bytes(path.read_bytes(), path=path)


def decode_utf8_bytes(data: bytes, *, path: Path) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        hint = _encoding_hint(data)
        detail = f"; file appears to be {hint}" if hint is not None else ""
        raise TextEncodingError(
            f"unsupported text encoding for {path}: expected UTF-8 text{detail} ({exc.reason} at byte {exc.start})"
        ) from exc


def _encoding_hint(data: bytes) -> str | None:
    if data.startswith(b"\xff\xfe"):
        return "UTF-16 LE"
    if data.startswith(b"\xfe\xff"):
        return "UTF-16 BE"
    if data.startswith(b"\xff\xfe\x00\x00"):
        return "UTF-32 LE"
    if data.startswith(b"\x00\x00\xfe\xff"):
        return "UTF-32 BE"
    return None
