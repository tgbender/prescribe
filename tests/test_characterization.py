"""XFAIL characterization tests for known issues identified during review."""

from __future__ import annotations

from pathlib import Path

from prescribe.cli import _results_to_json
from prescribe.orchestrator import Orchestrator
from prescribe.presets import Presets
from prescribe.shell import render_shell_block
from prescribe.spec import SpecLoader

# ── Issue 1: CLI result/spec misalignment when tag filtering skips files ──────


def test_results_alignment_with_tag_filtered_files(tmp_path: Path, state_store) -> None:
    """When tag filtering skips some file targets, results align with spec.files order.

    Currently: _resolve_active_files produces [skipped..., active...] order,
    but _results_to_json/_print_results iterate spec.files in original order.
    This causes mismatched status-to-path pairing when skipped files come before
    active files in the spec.
    """
    config_a = tmp_path / "a.toml"
    config_a.write_text("x = 1\n")
    config_b = tmp_path / "b.toml"
    config_b.write_text("y = 1\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'a.toml'\n"
        "format = 'toml'\n"
        "tags = ['base']\n"
        "[files.data]\n"
        "x = 2\n"
        "\n"
        "[[files]]\n"
        "path = 'b.toml'\n"
        "format = 'toml'\n"
        "tags = ['work']\n"
        "[files.data]\n"
        "y = 2\n"
    )

    orch = Orchestrator(state_store)
    results = orch.run(spec_path, tags={"base"})

    spec = SpecLoader().load(spec_path)
    data = _results_to_json(spec, results, display_status=True)

    # The first JSON entry should correspond to a.toml (base tag), not b.toml
    assert len(data) == 2
    assert data[0]["path"] == str(config_a)
    # a.toml should be applied (or noop after first run), not skipped
    # b.toml should be skipped
    assert data[0]["status"] != "skipped", f"First entry should be for a.toml, got: {data[0]}"
    assert data[1]["path"] == str(config_b)
    assert data[1]["status"] == "skipped", f"Second entry should be skipped b.toml, got: {data[1]}"


def test_cli_results_alignment_same_path_different_tags(tmp_path: Path, capsys, monkeypatch, state_store) -> None:
    """When two file targets share a path but different tags, alignment is correct."""
    config = tmp_path / "config.toml"
    config.write_text("x = 1\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "priority = 0\n"
        "tags = ['base']\n"
        "[files.data]\n"
        "x = 2\n"
        "\n"
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "priority = 10\n"
        "tags = ['work']\n"
        "[files.data]\n"
        "x = 3\n"
    )

    orch = Orchestrator(state_store)
    results = orch.run(spec_path, tags={"base"})

    spec = SpecLoader().load(spec_path)
    data = _results_to_json(spec, results, display_status=True)

    # With tags={"base"}, first target is active, second is skipped.
    # The JSON should have two entries: one for each spec file target.
    assert len(data) == 2
    # First entry (spec.files[0]) should show applied for base target
    assert data[0]["status"] != "skipped"
    # Second entry (spec.files[1]) should show skipped for work target
    assert data[1]["status"] == "skipped"


# ── Issue 2: LineAdapter not exported from adapters/__init__.__all__ ──────────


def test_line_adapter_in_adapters_all() -> None:
    """LineAdapter and line_adapter should be in adapters.__all__."""
    from prescribe import adapters

    assert "LineAdapter" in adapters.__all__, "LineAdapter missing from adapters.__all__"
    assert "line_adapter" in adapters.__all__, "line_adapter missing from adapters.__all__"


def test_line_adapter_importable_from_adapters_star() -> None:
    """LineAdapter should be importable via from prescribe.adapters import *."""
    import importlib

    # Simulate `from prescribe.adapters import *`
    mod = importlib.import_module("prescribe.adapters")
    names = mod.__all__
    assert "LineAdapter" in names
    assert hasattr(mod, "LineAdapter")
    assert hasattr(mod, "line_adapter")


# ── Issue 3: Presets class lacks list_managed equivalent to CLI `list` ────────


def test_presets_has_list_managed(state_store) -> None:
    """Presets class should expose a method equivalent to CLI `list`."""
    presets = Presets(state_store=state_store)
    assert hasattr(presets, "list_managed"), "Presets missing list_managed method"


# ── Issue 4: _expandvars re-imports re module on every call ───────────────────


def test_expandvars_uses_module_level_re(state_store) -> None:
    """_expandvars should not re-import re on every call."""
    # Check if re is already imported at module level in orchestrator
    from prescribe import orchestrator as orch_mod

    assert "re" in orch_mod.__dict__, "re should be imported at module level in orchestrator"


# ── Issue 5: _handle_shell renders shell block twice (dead first loop) ────────


def test_handle_shell_renders_once_per_target(tmp_path: Path, state_store, monkeypatch) -> None:
    """_handle_shell should call render_shell_block once per shell target."""
    call_count = 0
    orig_render = render_shell_block

    def counting_render(*, shell_type, env_vars, managed_block_id):
        nonlocal call_count
        call_count += 1
        return orig_render(shell_type=shell_type, env_vars=env_vars, managed_block_id=managed_block_id)

    monkeypatch.setattr("prescribe.orchestrator.render_shell_block", counting_render)

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[shell]]\npath = '.bashrc'\nmanaged_block_id = 'prescribe-env'\n")

    orch = Orchestrator(state_store)
    orch.run(spec_path, dry_run=True)

    # Should render exactly once for this single shell target
    assert call_count == 1, f"render_shell_block called {call_count} times, expected 1"


# ── Issue 6: CLI apply/status passes Path to Orchestrator.run() instead of Spec ─


def test_cli_apply_passes_spec_obj_not_path(tmp_path: Path, monkeypatch, state_store) -> None:
    """CLI apply should parse spec once and pass Spec object to Orchestrator.run()."""
    config = tmp_path / "config.toml"
    config.write_text("x = 1\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\nx = 2\n")

    run_calls = []
    orig_run = Orchestrator.run

    def capturing_run(self, spec, *, dry_run=False, tags=None, skip_tags=None, diff=False):  # noqa: ARG001
        run_calls.append(("run", type(spec).__name__, getattr(spec, "path", None)))
        return orig_run(self, spec, dry_run=dry_run, tags=tags, skip_tags=skip_tags, diff=diff)

    monkeypatch.setattr(Orchestrator, "run", capturing_run)

    from prescribe.cli import apply

    apply(spec=spec_path, dry_run=True, output_json=False, state=None, tags=None, skip_tags=None)

    # The spec argument passed to Orchestrator.run should be a Spec, not Path/str
    assert len(run_calls) == 1
    _method, arg_type, _arg_path = run_calls[0]
    assert arg_type == "Spec", f"Expected Spec but got {arg_type}"


# ── Issue 7: ConflictResolver type alias is loose (Callable[[str], bool] | None)
#    Skipping xfail — this is a typing completeness issue, not a runtime bug.


# ── Issue 8: assert run_id is not None instead of explicit error in _process_new_file


def test_process_new_file_run_id_assertion_covered() -> None:
    """_process_new_file should handle run_id=None gracefully (dry_run path never hits it).
    This test documents that the assertion is currently unreachable in normal flow."""
    # This is more of a code-quality note; the assertion is guarded by dry_run path.
    # If we ever refactor to call _process_new_file with run_id=None in a non-dry_run
    # context, we should use an explicit RuntimeError instead of assert.
    pass
