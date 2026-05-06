import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from prescribe.orchestrator import Orchestrator
from prescribe.spec import SpecLoader

_CLI = shutil.which("prescribe")
pytestmark = pytest.mark.cli


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def run(workdir: Path):
    state_db = workdir / ".prescribe" / "state.db"

    def _run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [_CLI, *args],
            capture_output=True,
            text=True,
            cwd=workdir,
            env={**os.environ, "PRESCRIBE_STATE": str(state_db)},
        )

    return _run


def test_spec_loader_parses_asset_file_target(tmp_path: Path) -> None:
    source = tmp_path / "repo" / "mcp.json"
    source.parent.mkdir()
    source.write_text("{}")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/mcp.json'\ndest = 'system/mcp.json'\n")

    spec = SpecLoader().load(spec_path)

    assert len(spec.assets) == 1
    assert spec.assets[0].source == str(source.resolve())
    assert spec.assets[0].dest == (tmp_path / "system" / "mcp.json").resolve()
    assert spec.assets[0].mode == "file"


def test_spec_loader_defaults_glob_asset_to_mirror(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'skills/**/*.md'\ndest = 'out/skills'\n")

    spec = SpecLoader().load(spec_path)

    assert spec.assets[0].mode == "mirror"
    assert spec.assets[0].source.endswith("skills\\**\\*.md") or spec.assets[0].source.endswith("skills/**/*.md")


def test_orchestrator_materializes_asset_file_and_rolls_back_creation(tmp_path: Path, state_store) -> None:
    source = tmp_path / "repo" / "mcp.json"
    source.parent.mkdir()
    source.write_text('{"server": "local"}\n')
    dest = tmp_path / "system" / "mcp.json"
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/mcp.json'\ndest = 'system/mcp.json'\n")

    orchestrator = Orchestrator(state_store)
    applied = orchestrator.run(spec_path)

    assert applied[0].status == "applied"
    assert dest.read_text() == '{"server": "local"}\n'

    second = orchestrator.run(spec_path)
    assert second[0].status == "noop"

    rolled_back = orchestrator.rollback(dest)
    assert rolled_back.status == "rolled-back"
    assert not dest.exists()


def test_orchestrator_mirrors_glob_asset_tree(tmp_path: Path, state_store) -> None:
    (tmp_path / "repo" / "skills" / "alpha").mkdir(parents=True)
    (tmp_path / "repo" / "skills" / "beta").mkdir(parents=True)
    (tmp_path / "repo" / "skills" / "alpha" / "SKILL.md").write_text("alpha\n")
    (tmp_path / "repo" / "skills" / "beta" / "SKILL.md").write_text("beta\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/skills/**/*.md'\ndest = 'system/skills'\n")

    applied = Orchestrator(state_store).run(spec_path)

    assert applied[0].status == "applied"
    assert (tmp_path / "system" / "skills" / "alpha" / "SKILL.md").read_text() == "alpha\n"
    assert (tmp_path / "system" / "skills" / "beta" / "SKILL.md").read_text() == "beta\n"


def test_orchestrator_asset_dry_run_diff_does_not_write(tmp_path: Path, state_store) -> None:
    source = tmp_path / "repo" / "profile.ps1"
    source.parent.mkdir()
    source.write_text("Set-Alias ll Get-ChildItem\n")
    dest = tmp_path / "system" / "profile.ps1"
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/profile.ps1'\ndest = 'system/profile.ps1'\n")

    result = Orchestrator(state_store).run(spec_path, dry_run=True, diff=True)[0]

    assert result.status == "dry-run"
    assert result.changed is True
    assert result.diff is not None
    assert "+Set-Alias ll Get-ChildItem" in result.diff
    assert not dest.exists()


def test_orchestrator_asset_conflicts_after_external_edit(tmp_path: Path, state_store) -> None:
    source = tmp_path / "repo" / "config.txt"
    source.parent.mkdir()
    source.write_text("one\n")
    dest = tmp_path / "system" / "config.txt"
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/config.txt'\ndest = 'system/config.txt'\n")

    orchestrator = Orchestrator(state_store)
    assert orchestrator.run(spec_path)[0].status == "applied"
    dest.write_text("manual\n")
    source.write_text("two\n")

    conflict = orchestrator.run(spec_path)[0]

    assert conflict.status == "conflict"
    assert conflict.conflict is not None
    assert conflict.conflict.reason == "content changed"
    assert dest.read_text() == "manual\n"


def test_cli_status_json_reports_asset_target(run, workdir: Path) -> None:
    source = workdir / "repo" / "mcp.json"
    source.parent.mkdir()
    source.write_text("{}\n")
    spec = workdir / "spec.toml"
    spec.write_text("[[assets]]\nsource = 'repo/mcp.json'\ndest = 'system/mcp.json'\n")

    result = run("status", "--json", str(spec))

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data[0]["type"] == "asset"
    assert data[0]["status"] == "would change"
    assert data[0]["mode"] == "file"
