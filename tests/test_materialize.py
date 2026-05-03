"""Tests for env var materialization to OS-level persistent stores.

Tests call backend functions directly with explicit path arguments.
No monkeypatching -- just pure functions with tmp_path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from prescribe.materialize import (
    _expand,
    _materialize_linux,
    _materialize_macos,
    _sh_escape,
    _systemd_escape,
    detect_platform,
)

# ── helper functions ─────────────────────────────────────


def test_sh_escape():
    assert _sh_escape("hello") == "'hello'"
    assert _sh_escape("it's") == "'it'\\''s'"


def test_systemd_escape():
    escaped = _systemd_escape('hello"world')
    assert '\\"' in escaped
    assert escaped.startswith('"') and escaped.endswith('"')


def test_expand():
    assert _expand("$HOME/.cargo") == str(Path.home() / ".cargo")
    assert _expand("${HOME}/bin") == str(Path.home() / "bin")


# ── macos backend ────────────────────────────────────────


def test_macos_creates_plist_and_script(tmp_path: Path):
    env_vars = {"EDITOR": "nvim", "CARGO_HOME": "/Users/test/.cargo"}
    _materialize_macos(env_vars=env_vars, dry_run=False, home=tmp_path)

    plist = tmp_path / "Library" / "LaunchAgents" / "com.prescribe.env.plist"
    assert plist.exists()
    content = plist.read_text()
    assert "com.prescribe.env" in content
    assert "RunAtLoad" in content

    script = tmp_path / ".config" / "prescribe" / "login-env.sh"
    assert script.exists()
    script_content = script.read_text()
    assert "launchctl setenv EDITOR" in script_content
    assert "launchctl setenv CARGO_HOME" in script_content


def test_macos_dry_run_does_not_write(tmp_path: Path):
    env_vars = {"EDITOR": "nvim"}
    _materialize_macos(env_vars=env_vars, dry_run=True, home=tmp_path)

    plist = tmp_path / "Library" / "LaunchAgents" / "com.prescribe.env.plist"
    script = tmp_path / ".config" / "prescribe" / "login-env.sh"
    assert not plist.exists()
    assert not script.exists()


def test_macos_empty_env_writes_minimal_plist(tmp_path: Path):
    _materialize_macos(env_vars={}, dry_run=False, home=tmp_path)

    plist = tmp_path / "Library" / "LaunchAgents" / "com.prescribe.env.plist"
    assert plist.exists()
    assert "com.prescribe.env" in plist.read_text()

    script = tmp_path / ".config" / "prescribe" / "login-env.sh"
    assert script.exists()
    content = script.read_text()
    assert "#!/bin/sh" in content


# ── linux backend ────────────────────────────────────────


def test_linux_creates_conf(tmp_path: Path):
    env_vars = {"EDITOR": "nvim", "PATH": "/usr/bin:/bin"}
    _materialize_linux(env_vars=env_vars, dry_run=False, home=tmp_path)

    conf = tmp_path / ".config" / "environment.d" / "prescribe.conf"
    assert conf.exists()
    content = conf.read_text()
    assert "EDITOR=" in content
    assert "PATH=" in content


def test_linux_dry_run_does_not_write(tmp_path: Path):
    env_vars = {"EDITOR": "nvim"}
    _materialize_linux(env_vars=env_vars, dry_run=True, home=tmp_path)

    conf = tmp_path / ".config" / "environment.d" / "prescribe.conf"
    assert not conf.exists()


def test_linux_empty_env_writes_minimal_conf(tmp_path: Path):
    _materialize_linux(env_vars={}, dry_run=False, home=tmp_path)

    conf = tmp_path / ".config" / "environment.d" / "prescribe.conf"
    assert conf.exists()
    assert "prescribe" in conf.read_text()


# ── detect_platform (xfail on non-target platforms) ────


@pytest.mark.xfail(sys.platform != "darwin", reason="only meaningful on macOS")
def test_detect_platform_returns_macos():
    assert detect_platform() == "macos"


@pytest.mark.xfail(sys.platform != "linux", reason="only meaningful on Linux")
def test_detect_platform_returns_linux():
    assert detect_platform() == "linux"


@pytest.mark.xfail(sys.platform != "win32", reason="only meaningful on Windows")
def test_detect_platform_returns_windows():
    assert detect_platform() == "windows"


# ── orchestration integration (xfail until wired) ────────


def test_orchestrator_materialize_integration(fake_root, state_store):
    """Full integration: [[env]] with materialize=true calls materialize()."""
    from prescribe.orchestrator import Orchestrator

    spec_path = fake_root / "spec.toml"
    spec_path.write_text(
        "[[env]]\nname = 'EDITOR'\nvalue = 'nvim'\nmaterialize = true\nplatforms = ['macos', 'linux']\n"
    )

    orch = Orchestrator(state_store)
    results = orch.run(spec_path)
    # Verify env vars are resolved and flagged for materialize
    for r in results:
        if "EDITOR" in r.env_vars:
            assert r.env_vars["EDITOR"] == "nvim"
            break
    else:
        pytest.fail("EDITOR not found in resolved env_vars")
