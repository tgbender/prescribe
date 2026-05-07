from __future__ import annotations

import pytest

from prescribe.path_policy import PathPolicyError, validate_portable_path


@pytest.mark.parametrize(
    "value",
    [
        "C:relative.txt",
        "NUL",
        "nul.txt",
        "COM1/profile.ps1",
        "profile. ",
        "profile.",
        "settings.json:stream",
        "dir/name\x1f.txt",
        r"\\?\C:\Users\Travis\config.toml",
        r"\\.\C:\Users\Travis\config.toml",
        f"{'a' * 256}.toml",
    ],
)
def test_validate_portable_path_rejects_unsafe_windows_forms(value: str) -> None:
    with pytest.raises(PathPolicyError):
        validate_portable_path(value)


@pytest.mark.parametrize(
    "value",
    [
        "config.toml",
        "../config.toml",
        "/home/travis/.config/tool.toml",
        r"C:\Users\Travis\.config\tool.toml",
        r"\\server\share\tool.toml",
        "a" * 255,
    ],
)
def test_validate_portable_path_accepts_common_paths(value: str) -> None:
    validate_portable_path(value)


def test_validate_portable_path_allows_globs_when_requested() -> None:
    validate_portable_path("repo/**/*.md", allow_glob=True)


def test_validate_portable_path_rejects_globs_by_default() -> None:
    with pytest.raises(PathPolicyError):
        validate_portable_path("repo/**/*.md")
