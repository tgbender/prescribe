import json
import os
import shutil
import subprocess
from pathlib import Path
from stat import S_IMODE

import pytest

from prescribe.orchestrator import Orchestrator
from prescribe.spec import SpecLoader

_CLI = shutil.which("prescribe")
pytestmark = pytest.mark.cli


@pytest.fixture(autouse=True)
def _prescribe_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRESCRIBE_DATA_DIR", str(tmp_path / ".prescribe-data"))


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


def test_spec_loader_asset_uses_first_existing_path(tmp_path: Path) -> None:
    first = tmp_path / "missing" / "mcp.json"
    second = tmp_path / "existing" / "mcp.json"
    second.parent.mkdir()
    second.write_text("{}\n")
    source = tmp_path / "repo" / "mcp.json"
    source.parent.mkdir()
    source.write_text("{}\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        f"[[assets]]\nsource = 'repo/mcp.json'\ndest = 'fallback/mcp.json'\npaths = ['{first}', '{second}']\n"
    )

    spec = SpecLoader().load(spec_path)

    assert spec.assets[0].dest == second


def test_spec_loader_asset_uses_dest_location_with_append(tmp_path: Path) -> None:
    source = tmp_path / "repo" / "mcp.json"
    source.parent.mkdir()
    source.write_text("{}\n")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[locations.agent]\n"
        "kind = 'dir'\n"
        "candidates = ['config']\n"
        "\n"
        "[[assets]]\n"
        "source = 'repo/mcp.json'\n"
        "dest_location = 'agent'\n"
        "dest_append = 'mcp.json'\n"
    )

    spec = SpecLoader().load(spec_path)

    assert spec.assets[0].dest == (config_dir / "mcp.json").resolve()


def test_spec_loader_rejects_replace_for_file_mode(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/mcp.json'\ndest = 'out/mcp.json'\nreplace = true\n")

    with pytest.raises(Exception, match="replace is only supported"):
        SpecLoader().load(spec_path)


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


def test_orchestrator_asset_rollback_restores_existing_file(tmp_path: Path, state_store) -> None:
    source = tmp_path / "repo" / "config.txt"
    source.parent.mkdir()
    source.write_text("managed\n")
    dest = tmp_path / "system" / "config.txt"
    dest.parent.mkdir()
    dest.write_text("original\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/config.txt'\ndest = 'system/config.txt'\n")

    orchestrator = Orchestrator(state_store)
    assert orchestrator.run(spec_path)[0].status == "applied"
    assert dest.read_text() == "managed\n"

    rolled_back = orchestrator.rollback(dest)

    assert rolled_back.status == "rolled-back"
    assert dest.read_text() == "original\n"


def test_orchestrator_asset_preserves_source_lf_bytes_on_windows(tmp_path: Path, state_store) -> None:
    source = tmp_path / "repo" / "profile.ps1"
    source.parent.mkdir()
    source.write_bytes(b"$env:EDITOR = 'nvim'\n")
    dest = tmp_path / "system" / "profile.ps1"
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/profile.ps1'\ndest = 'system/profile.ps1'\n")

    applied = Orchestrator(state_store).run(spec_path)[0]

    assert applied.status == "applied"
    assert dest.read_bytes() == b"$env:EDITOR = 'nvim'\n"


def test_orchestrator_asset_rollback_restores_crlf_bytes(tmp_path: Path, state_store) -> None:
    source = tmp_path / "repo" / "config.txt"
    source.parent.mkdir()
    source.write_bytes(b"managed\n")
    dest = tmp_path / "system" / "config.txt"
    dest.parent.mkdir()
    dest.write_bytes(b"original\r\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/config.txt'\ndest = 'system/config.txt'\n")

    orchestrator = Orchestrator(state_store)
    assert orchestrator.run(spec_path)[0].status == "applied"
    assert dest.read_bytes() == b"managed\n"

    rolled_back = orchestrator.rollback(dest)

    assert rolled_back.status == "rolled-back"
    assert dest.read_bytes() == b"original\r\n"


@pytest.mark.skipif(os.name == "nt", reason="Windows chmod does not preserve POSIX mode bits")
def test_orchestrator_asset_replacing_existing_file_preserves_destination_mode(tmp_path: Path, state_store) -> None:
    source = tmp_path / "repo" / "script.sh"
    source.parent.mkdir()
    source.write_bytes(b"#!/bin/sh\necho managed\n")
    source.chmod(0o755)
    dest = tmp_path / "system" / "script.sh"
    dest.parent.mkdir()
    dest.write_bytes(b"#!/bin/sh\necho original\n")
    dest.chmod(0o600)
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/script.sh'\ndest = 'system/script.sh'\n")

    applied = Orchestrator(state_store).run(spec_path)[0]

    assert applied.status == "applied"
    assert S_IMODE(dest.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="Windows chmod does not preserve POSIX mode bits")
def test_orchestrator_asset_creating_file_uses_source_mode(tmp_path: Path, state_store) -> None:
    source = tmp_path / "repo" / "script.sh"
    source.parent.mkdir()
    source.write_bytes(b"#!/bin/sh\necho managed\n")
    source.chmod(0o755)
    dest = tmp_path / "system" / "script.sh"
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/script.sh'\ndest = 'system/script.sh'\n")

    applied = Orchestrator(state_store).run(spec_path)[0]

    assert applied.status == "applied"
    assert S_IMODE(dest.stat().st_mode) == 0o755


@pytest.mark.skipif(os.name == "nt", reason="Windows chmod does not preserve POSIX mode bits")
def test_orchestrator_asset_rollback_restores_destination_mode(tmp_path: Path, state_store) -> None:
    source = tmp_path / "repo" / "config.txt"
    source.parent.mkdir()
    source.write_bytes(b"managed\n")
    source.chmod(0o644)
    dest = tmp_path / "system" / "config.txt"
    dest.parent.mkdir()
    dest.write_bytes(b"original\n")
    dest.chmod(0o600)
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/config.txt'\ndest = 'system/config.txt'\n")

    orchestrator = Orchestrator(state_store)
    assert orchestrator.run(spec_path)[0].status == "applied"
    dest.chmod(0o644)

    rolled_back = orchestrator.rollback(dest)

    assert rolled_back.status == "rolled-back"
    assert S_IMODE(dest.stat().st_mode) == 0o600


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


def test_orchestrator_mirror_rollback_restores_existing_nested_file(tmp_path: Path, state_store) -> None:
    (tmp_path / "repo" / "skills" / "alpha").mkdir(parents=True)
    (tmp_path / "repo" / "skills" / "alpha" / "SKILL.md").write_text("managed\n")
    existing = tmp_path / "system" / "skills" / "alpha" / "SKILL.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("original\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/skills/**/*.md'\ndest = 'system/skills'\n")

    orchestrator = Orchestrator(state_store)
    assert orchestrator.run(spec_path)[0].status == "applied"
    assert existing.read_text() == "managed\n"

    rolled_back = orchestrator.rollback(existing)

    assert rolled_back.status == "rolled-back"
    assert existing.read_text() == "original\n"


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


def test_orchestrator_asset_replace_moves_extra_file_to_backup_and_restores(tmp_path: Path, state_store) -> None:
    (tmp_path / "repo" / "skills" / "alpha").mkdir(parents=True)
    (tmp_path / "repo" / "skills" / "alpha" / "SKILL.md").write_text("managed\n")
    extra = tmp_path / "system" / "skills" / "old.md"
    extra.parent.mkdir(parents=True)
    extra.write_text("old\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/skills/**/*.md'\ndest = 'system/skills'\nreplace = true\n")

    orchestrator = Orchestrator(state_store)
    applied = orchestrator.run(spec_path)

    assert applied[0].status == "applied"
    assert not extra.exists()
    backups = state_store.asset_backups(extra)
    assert len(backups) == 1
    assert backups[0].backup_path.exists()
    assert backups[0].backup_path.read_text() == "old\n"

    restored = orchestrator.rollback(extra)

    assert restored.status == "restored"
    assert extra.read_text() == "old\n"
    assert not backups[0].backup_path.exists()


def test_orchestrator_asset_replace_dry_run_reports_extra_without_moving(tmp_path: Path, state_store) -> None:
    (tmp_path / "repo" / "skills").mkdir(parents=True)
    (tmp_path / "repo" / "skills" / "SKILL.md").write_text("managed\n")
    extra = tmp_path / "system" / "skills" / "old.md"
    extra.parent.mkdir(parents=True)
    extra.write_text("old\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/skills/**/*.md'\ndest = 'system/skills'\nreplace = true\n")

    result = Orchestrator(state_store).run(spec_path, dry_run=True, diff=True)[0]

    assert result.status == "dry-run"
    assert "would move extra files to backup" in (result.diff or "")
    assert str(extra) in (result.diff or "")
    assert extra.read_text() == "old\n"
    assert state_store.asset_backups(extra) == []


def test_orchestrator_asset_replace_refuses_home_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state_store
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "managed.txt").write_text("managed\n")
    (home / "extra.txt").write_text("old\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/*.txt'\ndest = '~'\nreplace = true\n")

    result = Orchestrator(state_store).run(spec_path)[0]

    assert result.status == "error"
    assert "too broad" in (result.error or "")
    assert (home / "extra.txt").read_text() == "old\n"


def test_orchestrator_asset_replace_refuses_large_extra_file(tmp_path: Path, state_store) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "managed.txt").write_text("managed\n")
    extra = tmp_path / "system" / "extra.txt"
    extra.parent.mkdir()
    extra.write_text("old\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/*.txt'\ndest = 'system'\nreplace = true\nmax_displace_bytes = 1\n")

    result = Orchestrator(state_store).run(spec_path)[0]

    assert result.status == "error"
    assert "exceeds max_displace_bytes" in (result.error or "")
    assert extra.read_text() == "old\n"
    assert not (tmp_path / "system" / "managed.txt").exists()


def test_orchestrator_asset_replace_refuses_binary_extra_file(tmp_path: Path, state_store) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "managed.txt").write_text("managed\n")
    extra = tmp_path / "system" / "extra.bin"
    extra.parent.mkdir()
    extra.write_bytes(b"a\0b")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/*.txt'\ndest = 'system'\nreplace = true\n")

    result = Orchestrator(state_store).run(spec_path)[0]

    assert result.status == "error"
    assert "binary-looking" in (result.error or "")
    assert extra.read_bytes() == b"a\0b"
    assert not (tmp_path / "system" / "managed.txt").exists()


def test_orchestrator_asset_replaces_symlink_without_mutating_target(tmp_path: Path, state_store) -> None:
    source = tmp_path / "repo" / "config.txt"
    source.parent.mkdir()
    source.write_text("managed\n")
    real = tmp_path / "real.txt"
    real.write_text("real\n")
    dest = tmp_path / "system" / "config.txt"
    dest.parent.mkdir()
    try:
        dest.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/config.txt'\ndest = 'system/config.txt'\n")

    orchestrator = Orchestrator(state_store)
    applied = orchestrator.run(spec_path)[0]

    assert applied.status == "applied"
    assert real.read_text() == "real\n"
    assert not dest.is_symlink()
    assert dest.read_text() == "managed\n"

    rolled_back = orchestrator.rollback(dest)

    assert rolled_back.status == "rolled-back"
    assert dest.is_symlink()
    assert dest.resolve() == real.resolve()


def test_orchestrator_asset_replaces_hardlink_without_mutating_other_name(tmp_path: Path, state_store) -> None:
    source = tmp_path / "repo" / "config.txt"
    source.parent.mkdir()
    source.write_text("managed\n")
    real = tmp_path / "real.txt"
    real.write_text("real\n")
    dest = tmp_path / "system" / "config.txt"
    dest.parent.mkdir()
    try:
        os.link(real, dest)
    except OSError as exc:
        pytest.skip(f"hardlink creation unavailable: {exc}")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/config.txt'\ndest = 'system/config.txt'\n")

    applied = Orchestrator(state_store).run(spec_path)[0]

    assert applied.status == "applied"
    assert real.read_text() == "real\n"
    assert dest.read_text() == "managed\n"
    assert dest.stat().st_nlink == 1


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
