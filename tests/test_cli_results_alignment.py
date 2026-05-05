"""Characterization tests for CLI results alignment."""

from pathlib import Path

from prescribe.cli import _results_to_json
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
