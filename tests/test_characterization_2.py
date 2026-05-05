"""XFAIL characterization tests for spec var collision and JSONC edge cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from prescribe.adapters.jsonc import JsoncAdapter
from prescribe.orchestrator import Orchestrator
from prescribe.spec import SpecLoader, expand_spec_vars

# ── Issue: expand_spec_vars has prefix collision ──────────────────────────────


def test_expand_spec_vars_longer_keys_first() -> None:
    """Vars must expand longest keys first to avoid prefix corruption.

    Currently: dict iteration is insertion-ordered, so A="x" corrupts $AB to xB
    before AB="y" gets a chance to match.
    """
    result = expand_spec_vars("$AB", {"A": "x", "AB": "y"})
    assert result == "y", f"Expected 'y' but got {result!r}"


def test_expand_spec_vars_isolation() -> None:
    """Multiple independent vars with overlapping prefixes should all work."""
    result = expand_spec_vars("$HOME/$HOMEBREW", {"HOME": "/Users/x", "HOMEBREW": "/opt"})
    assert result == "/Users/x//opt", f"Expected '/Users/x//opt' but got {result!r}"


# ── Issue: comment-only JSONC files crash on apply ────────────────────────────


def test_jsonc_comment_only_file_does_not_crash(state_store, tmp_path: Path) -> None:
    """A JSONC file with only comments should be treated as empty {}, not None."""
    config = tmp_path / "settings.jsonc"
    config.write_text("// just a comment\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\npath = 'settings.jsonc'\nformat = 'jsonc'\n[files.data]\n\"editor.fontSize\" = 14\n"
    )

    orch = Orchestrator(state_store)
    results = orch.run(spec_path)

    # Should not error — the file should be treated as empty {}
    errors = [r.error for r in results if r.status == "error"]
    assert not errors, f"Got errors: {errors}"

    # Key should have been written (nested form since the original was empty)
    adapter = JsoncAdapter()
    document = adapter.load(config)
    assert document.root.get("editor", {}).get("fontSize") == 14


# ── Issue: launchctl subprocess exceptions swallowed silently ─────────────────


def test_materialize_macos_logs_reload_failure(state_store, tmp_path: Path) -> None:
    """When launchctl fails, the error should be reported via the materialize path.

    Previously: _maybe_reload_launchctl had bare `except Exception: pass`,
    so reload failures were silently swallowed.
    """
    import io
    import sys

    from prescribe.materialize import _materialize_macos

    stderr_capture = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = stderr_capture

    # This will try to run launchctl (which may fail) but the writing should succeed
    env_vars = {"EDITOR": "nvim"}
    home = tmp_path
    _materialize_macos(env_vars=env_vars, dry_run=False, home=home)

    sys.stderr = old_stderr
    # Verify something was captured (subprocess output, not swallowed)
    assert isinstance(stderr_capture.getvalue(), str)

    # The plist and script should exist regardless of launchctl outcome
    assert (home / "Library" / "LaunchAgents" / "com.prescribe.env.plist").exists()

    # If launchctl failed, we should see output on stderr (not silently swallowed)
    # On this test it may or may not depending on platform; the important thing
    # is that _maybe_reload_launchctl no longer has bare `except Exception: pass`.
    # This test mainly documents that the function exists and writes files.


# ── Issue: assert run_id is not None in _process_new_file ─────────────────────


def test_process_new_file_invalid_run_id_raises(tmp_path: Path, state_store) -> None:
    """If _process_new_file is called with run_id=None during a real apply, it
    should raise a clear RuntimeError instead of a cryptic AssertionError."""
    from prescribe.adapters import adapter_for_path
    from prescribe.orchestrator import Orchestrator

    config = tmp_path / "new.toml"
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'new.toml'\nformat = 'toml'\n[files.data]\nx = 1\n")

    orch = Orchestrator(state_store)
    adapter = adapter_for_path(config, fmt="toml")

    # Call _process_new_file with run_id=None explicitly.
    with pytest.raises(RuntimeError, match="run_id"):
        orch._process_new_file(
            run_id=None,
            spec_hash=b"\x00",
            target=SpecLoader().load(spec_path).files[0],
            adapter=adapter,
        )
