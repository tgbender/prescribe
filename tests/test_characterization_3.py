"""Characterization tests for assert-to-runtime, suppress removal, pathsep."""

from __future__ import annotations

from pathlib import Path

import pytest

# ── Issue 1: Production assert statements survive -O builds ───────────────────


def test_process_new_file_assert_becomes_runtime_error(state_store, tmp_path: Path) -> None:
    """_process_new_file must raise RuntimeError, not AssertionError, for invalid state."""
    from prescribe.adapters import adapter_for_path
    from prescribe.orchestrator import Orchestrator
    from prescribe.spec import SpecLoader

    config = tmp_path / "new.toml"
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'new.toml'\nformat = 'toml'\n[files.data]\nx = 1\n")

    orch = Orchestrator(state_store)
    adapter = adapter_for_path(config, fmt="toml")

    with pytest.raises(RuntimeError, match="run_id"):
        orch._process_new_file(
            run_id=None,
            spec_hash=b"\x00",
            target=SpecLoader().load(spec_path).files[0],
            adapter=adapter,
        )


def test_apply_shell_block_assert_becomes_runtime_error(state_store, tmp_path: Path) -> None:
    """_apply_shell_block should report RuntimeError via error result, not silently
    crash with AssertionError under -O."""
    from prescribe.orchestrator import Orchestrator
    from prescribe.spec import ShellTarget

    orch = Orchestrator(state_store)
    path = tmp_path / ".bashrc"
    path.write_text("# hello\n")
    target = ShellTarget(path=path, managed_block_id="test")

    # The RuntimeError is caught by the outer try/except and returned as an error result
    result = orch._apply_shell_block(
        run_id=None,
        spec_hash=b"\x00",
        target=target,
        rendered_lines=["export X=1"],
        dry_run=False,
    )
    assert result.status == "error"
    assert "run_id" in (result.error or "")


def test_apply_and_record_assert_becomes_runtime_error(state_store, tmp_path: Path) -> None:
    """_apply_and_record must raise RuntimeError, not AssertionError, for invalid state."""
    from prescribe.orchestrator import Orchestrator
    from prescribe.spec import FileTarget

    config = tmp_path / "config.toml"
    config.write_text("x = 1\n")

    orch = Orchestrator(state_store)

    from prescribe.adapters.toml import toml_adapter

    document = toml_adapter.load(config)

    target = FileTarget(path=config, format="toml")

    with pytest.raises(RuntimeError, match="run_id"):
        orch._apply_and_record(
            run_id=None,
            spec_hash=b"\x00",
            target=target,
            document=document,
            operations=[],
            original_exists=True,
            adapter=toml_adapter,
            event_type="applied",
        )


def test_sqlite_inserts_use_runtime_error_not_assert() -> None:
    """StateStore INSERT methods must use RuntimeError, not assert.

    The test inspects the source code for `assert cursor.lastrowid`.
    """
    import inspect

    from prescribe.state import sqlite as sqlite_mod

    source = inspect.getsource(sqlite_mod)
    assert "assert cursor.lastrowid is not None" not in source


# ── Issue 2: materialize suppressions silently swallow errors ─────────────────


def test_maybe_reload_launchctl_no_bare_except(tmp_path: Path) -> None:
    """_maybe_reload_launchctl should not have bare `except Exception: pass`."""
    import inspect

    from prescribe.materialize import _maybe_reload_launchctl

    source = inspect.getsource(_maybe_reload_launchctl)
    # Should not contain bare `except Exception: pass` or broad suppress
    assert "except Exception:\n        pass" not in source
    assert "except Exception:\n            pass" not in source
    # Should surface errors rather than silently swallow


def test_maybe_import_systemd_no_bare_except() -> None:
    """_maybe_import_systemd should not have bare `except Exception: pass`."""
    import inspect

    from prescribe.materialize import _maybe_import_systemd

    source = inspect.getsource(_maybe_import_systemd)
    assert "except Exception:\n        pass" not in source
    assert "except Exception:\n            pass" not in source


def test_materialize_windows_no_bare_except() -> None:
    """_materialize_windows should not have bare `except Exception: pass`."""
    import inspect

    from prescribe.materialize import _materialize_windows

    source = inspect.getsource(_materialize_windows)
    assert "except Exception:\n        pass" not in source
    assert "except Exception:\n            pass" not in source


# ── Issue 3: _split_path hardcodes colon, breaks Windows PATH ─────────────────


def test_split_path_uses_os_pathsep() -> None:
    """_split_path must use os.pathsep, not hardcoded ':'."""
    import os

    from prescribe.shell import _split_path

    # On POSIX: os.pathsep == ':'
    # On Windows: os.pathsep == ';'
    if os.name == "nt":
        result = _split_path(r"C:\Users\x\bin;C:\Program Files\bin")
        assert result == [r"C:\Users\x\bin", r"C:\Program Files\bin"]
    else:
        result = _split_path("/usr/local/bin:/usr/bin:/bin")
        assert result == ["/usr/local/bin", "/usr/bin", "/bin"]


def test_split_path_filters_empty() -> None:
    """_split_path should filter empty entries."""
    from prescribe.shell import _split_path

    result = _split_path(":usr/local::/usr:")
    assert result == ["usr/local", "/usr"]
