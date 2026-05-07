from __future__ import annotations

import re

_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

MAX_WINDOWS_COMPONENT_LENGTH = 255
_DRIVE_RELATIVE_RE = re.compile(r"^[A-Za-z]:(?:$|[^\\/])")


class PathPolicyError(ValueError):
    pass


def validate_portable_path(value: str, *, allow_glob: bool = False) -> None:
    """Reject path strings that are unsafe or non-portable for managed files."""
    if not value:
        raise PathPolicyError("path must be non-empty")
    if any(ord(char) < 32 for char in value):
        raise PathPolicyError("path contains control characters")
    if _has_windows_device_prefix(value):
        raise PathPolicyError("Windows device and extended-length path prefixes are not supported")
    if _DRIVE_RELATIVE_RE.match(value):
        raise PathPolicyError("drive-relative paths like 'C:foo' are not supported")
    if not allow_glob and any(char in value for char in "*?["):
        raise PathPolicyError("glob characters are not supported in this path")

    body = _strip_windows_drive(value)
    for component in _path_components(body):
        if component in {"", ".", ".."}:
            continue
        if component.endswith((" ", ".")):
            raise PathPolicyError(f"path component {component!r} must not end with a space or dot")
        if len(component) > MAX_WINDOWS_COMPONENT_LENGTH:
            raise PathPolicyError(
                f"path component {component!r} exceeds Windows component limit {MAX_WINDOWS_COMPONENT_LENGTH}"
            )
        if ":" in component:
            raise PathPolicyError("Windows alternate data streams are not supported")
        name = component.split(".", 1)[0].casefold().upper()
        if name in _RESERVED_WINDOWS_NAMES:
            raise PathPolicyError(f"Windows reserved device name is not supported: {component!r}")


def _strip_windows_drive(value: str) -> str:
    if len(value) >= 2 and value[1] == ":" and value[0].isalpha():
        return value[2:]
    return value


def _has_windows_device_prefix(value: str) -> bool:
    return value.startswith(("\\\\?\\", "\\\\.\\")) or value.startswith(("//?/", "//./"))


def _path_components(value: str) -> list[str]:
    return [component for component in re.split(r"[\\/]+", value) if component]
