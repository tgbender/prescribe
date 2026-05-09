"""Characterization tests for CLI results alignment."""

import sqlite3
from pathlib import Path

import pytest
import typer

from prescribe.cli import _print_results, _results_to_json, _run_app, _validation_targets
from prescribe.core.result import OrchestrationResult
from prescribe.spec import EnvTarget, FileTarget, ShellTarget, Spec


def _make_spec_with_all_three() -> Spec:
    return Spec(
        files=[FileTarget(path=Path("/home/user/.gitconfig"), format="toml", data={"core.editor": "nvim"})],
        env=[
            EnvTarget(name="EDITOR", value="nvim"),
            EnvTarget(name="PAGER", value="less"),
        ],
        shell=[
            ShellTarget(path=Path("/home/user/.bashrc"), managed_block_id="prescribe-env"),
        ],
    )


def _make_results() -> list[OrchestrationResult]:
    """Simulate what Orchestrator._run_all returns for a spec with files+env+shell.

    Only one result per file and per shell. Env targets are merged and do not
    produce individual results unless there are no files or shells.
    """
    return [
        OrchestrationResult(status="applied", applied=True, changed=True),
        OrchestrationResult(status="applied", applied=True, changed=True),
    ]


def test_results_to_json_includes_shell_when_env_present() -> None:
    spec = _make_spec_with_all_three()
    results = _make_results()
    data = _results_to_json(spec, results, display_status=False)

    types = [entry["type"] for entry in data]
    assert "file" in types
    assert "shell" in types


def test_results_to_json_has_correct_number_of_entries() -> None:
    spec = _make_spec_with_all_three()
    results = _make_results()
    data = _results_to_json(spec, results, display_status=False)

    assert len(data) == 2  # 1 file + 1 shell


def test_print_results_counts_resolved_env_in_mixed_spec(capsys) -> None:
    spec = Spec(
        files=[FileTarget(path=Path("config.toml"), format="toml")],
        env=[EnvTarget(name="EDITOR", value="nvim")],
    )
    results = [OrchestrationResult(status="noop", applied=False, changed=False, env_vars={"EDITOR": "nvim"})]

    _print_results(spec, results)

    output = capsys.readouterr().out
    assert "env" in output
    assert "1 active" in output
    assert "none" not in output


def test_validation_plan_marks_directory_priority_skip_as_skipped(tmp_path: Path) -> None:
    target = FileTarget(path=tmp_path / "config.toml", format="toml", data={"value": "base"})
    spec = Spec(files=[target])

    [entry] = _validation_targets(
        spec,
        explain_skips=True,
        plan=True,
        file_skip_reasons={id(target): "lower priority target selected"},
    )

    assert entry["active"] is False
    assert entry["status"] == "skipped"
    assert entry["changed"] is False
    assert entry["skip_reason"] == "lower priority target selected"


def test_main_reports_locked_state_without_traceback(capsys) -> None:
    def raise_locked() -> None:
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(typer.Exit) as exc:
        _run_app(raise_locked)

    assert exc.value.exit_code == 1
    assert "state database is locked" in capsys.readouterr().err


def test_run_level_claim_conflict_does_not_print_env_summary(capsys) -> None:
    spec = Spec(
        files=[FileTarget(path=Path("config.toml"), format="toml")],
        env=[EnvTarget(name="EDITOR", value="nvim")],
    )
    results = [
        OrchestrationResult(
            status="error",
            applied=False,
            changed=False,
            error="claim conflict: file config.toml count is managed by other-spec#files[0]",
        )
    ]

    _print_results(spec, results)

    output = capsys.readouterr().out
    assert "claim conflict" in output
    assert "env" not in output


def test_run_level_claim_conflict_json_is_not_attached_to_first_file() -> None:
    spec = Spec(
        files=[FileTarget(path=Path("config.toml"), format="toml")],
        env=[EnvTarget(name="EDITOR", value="nvim")],
    )
    results = [
        OrchestrationResult(
            status="error",
            applied=False,
            changed=False,
            error="claim conflict: file config.toml count is managed by other-spec#files[0]",
        )
    ]

    data = _results_to_json(spec, results)

    assert data == [
        {
            "type": "error",
            "status": "error",
            "applied": False,
            "changed": False,
            "error": "claim conflict: file config.toml count is managed by other-spec#files[0]",
        }
    ]


@pytest.mark.parametrize(
    "message",
    [
        "another prescribe write is active: other until 2026-01-01T00:00:00+00:00",
        "claim reservation conflict: file config.toml count is reserved by other-spec#files[0]",
        "portable path collision: Config.toml and config.toml refer to the same case-insensitive path",
    ],
)
def test_run_level_orchestration_errors_are_not_attached_to_first_file(message: str) -> None:
    spec = Spec(files=[FileTarget(path=Path("config.toml"), format="toml")])
    results = [OrchestrationResult(status="error", applied=False, changed=False, error=message)]

    data = _results_to_json(spec, results)

    assert data[0]["type"] == "error"
    assert "path" not in data[0]
    assert data[0]["error"] == message
