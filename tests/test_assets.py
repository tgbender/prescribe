import json
import os
import shutil
import subprocess
from pathlib import Path
from stat import S_IMODE

import pytest

from prescribe._util import sha256_bytes
from prescribe.orchestrator import Orchestrator
from prescribe.rollback import perform_rollback
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
    source.write_bytes(b"managed\n")
    dest = tmp_path / "system" / "config.txt"
    dest.parent.mkdir()
    dest.write_bytes(b"original\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/config.txt'\ndest = 'system/config.txt'\n")

    orchestrator = Orchestrator(state_store)
    assert orchestrator.run(spec_path)[0].status == "applied"
    assert dest.read_text() == "managed\n"

    rolled_back = orchestrator.rollback(dest)

    assert rolled_back.status == "rolled-back"
    assert dest.read_text() == "original\n"


def test_orchestrator_asset_apply_records_recovery_backup_before_replacing_existing_file(
    tmp_path: Path, state_store
) -> None:
    source = tmp_path / "repo" / "config.txt"
    source.parent.mkdir()
    source.write_bytes(b"managed\n")
    dest = tmp_path / "system" / "config.txt"
    dest.parent.mkdir()
    dest.write_bytes(b"original\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/config.txt'\ndest = 'system/config.txt'\n")

    applied = Orchestrator(state_store).run(spec_path)[0]

    assert applied.status == "applied"
    backups = state_store.recovery_backups(dest)
    assert len(backups) == 1
    assert backups[0].target_kind == "asset"
    assert backups[0].operation == "asset-write"
    assert backups[0].content_text == "original\n"
    assert backups[0].backup_path.read_bytes() == b"original\n"


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


def test_orchestrator_asset_reapply_after_rollback_does_not_conflict(tmp_path: Path, state_store) -> None:
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
    assert orchestrator.rollback(dest).status == "rolled-back"
    assert dest.read_text() == "original\n"

    reapplied = orchestrator.run(spec_path)[0]

    assert reapplied.status == "applied"
    assert dest.read_text() == "managed\n"


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


def test_orchestrator_asset_refuses_symlink_destination_without_mutating_target(tmp_path: Path, state_store) -> None:
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
    result = orchestrator.run(spec_path)[0]

    assert result.status == "error"
    assert "symlink" in (result.error or "")
    assert real.read_text() == "real\n"
    assert dest.is_symlink()
    assert dest.resolve() == real.resolve()


def test_orchestrator_asset_refuses_symlink_source(tmp_path: Path, state_store) -> None:
    real = tmp_path / "real.txt"
    real.write_text("real\n")
    source = tmp_path / "repo" / "config.txt"
    source.parent.mkdir()
    try:
        source.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    dest = tmp_path / "system" / "config.txt"
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/config.txt'\ndest = 'system/config.txt'\n")

    result = Orchestrator(state_store).run(spec_path)[0]

    assert result.status == "error"
    assert "symlink" in (result.error or "")
    assert not dest.exists()


def test_orchestrator_asset_replace_refuses_symlink_extra(tmp_path: Path, state_store) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "managed.txt").write_text("managed\n")
    real = tmp_path / "real.txt"
    real.write_text("real\n")
    extra = tmp_path / "system" / "extra.txt"
    extra.parent.mkdir()
    try:
        extra.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/*.txt'\ndest = 'system'\nreplace = true\n")

    result = Orchestrator(state_store).run(spec_path)[0]

    assert result.status == "error"
    assert "symlink" in (result.error or "")
    assert real.read_text() == "real\n"
    assert extra.is_symlink()
    assert not (tmp_path / "system" / "managed.txt").exists()


def test_orchestrator_asset_replace_refuses_extra_directory(tmp_path: Path, state_store) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "managed.txt").write_text("managed\n")
    extra_dir = tmp_path / "system" / "old"
    extra_dir.mkdir(parents=True)
    (extra_dir / "keep.txt").write_text("keep\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/*.txt'\ndest = 'system'\nreplace = true\n")

    result = Orchestrator(state_store).run(spec_path)[0]

    assert result.status == "error"
    assert "will not displace directories" in (result.error or "")
    assert (extra_dir / "keep.txt").read_text() == "keep\n"
    assert not (tmp_path / "system" / "managed.txt").exists()


def test_orchestrator_asset_preflights_all_destinations_before_writing(tmp_path: Path, state_store) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "a.txt").write_text("managed a\n")
    (tmp_path / "repo" / "b.txt").write_text("managed b\n")
    first = tmp_path / "system" / "a.txt"
    first.parent.mkdir()
    first.write_text("original a\n")
    real = tmp_path / "real-b.txt"
    real.write_text("real b\n")
    unsafe = tmp_path / "system" / "b.txt"
    try:
        unsafe.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/*.txt'\ndest = 'system'\n")

    result = Orchestrator(state_store).run(spec_path)[0]

    assert result.status == "error"
    assert "symlink" in (result.error or "")
    assert first.read_text() == "original a\n"
    assert real.read_text() == "real b\n"
    assert unsafe.is_symlink()


def test_orchestrator_asset_backup_restore_refuses_symlink_reappearance(tmp_path: Path, state_store) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "managed.txt").write_text("managed\n")
    extra = tmp_path / "system" / "extra.txt"
    extra.parent.mkdir()
    extra.write_text("old\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/*.txt'\ndest = 'system'\nreplace = true\n")
    orchestrator = Orchestrator(state_store)
    assert orchestrator.run(spec_path)[0].status == "applied"
    backups = state_store.asset_backups(extra)
    assert len(backups) == 1
    real = tmp_path / "real-extra.txt"
    real.write_text("real\n")
    try:
        extra.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    restored = orchestrator.rollback(extra)

    assert restored.status == "error"
    assert "symlink" in (restored.error or "")
    assert real.read_text() == "real\n"
    assert extra.is_symlink()
    assert backups[0].restored_at is None
    assert backups[0].backup_path.exists()


def test_rollback_refuses_symlink_parent_before_writing_file(tmp_path: Path, state_store) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "config.toml").write_text("count = 2\n")
    system = tmp_path / "system"
    try:
        system.symlink_to(victim, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    target = system / "config.toml"
    run = state_store.start_run(command="seed")
    state_store.record_change_batch(
        run_id=run.id,
        path=target,
        operations=[
            {
                "kind": "update",
                "key": "count",
                "value": 2,
                "before_value": 1,
            }
        ],
        original_exists=True,
        format="toml",
    )

    rolled_back = Orchestrator(state_store).rollback(target)

    assert rolled_back.status == "error"
    assert "parent path redirects" in (rolled_back.error or "")
    assert (victim / "config.toml").read_text() == "count = 2\n"


def test_asset_backup_restore_marks_successful_partial_restore(tmp_path: Path, state_store) -> None:
    target = tmp_path / "system"
    extra_a = target / "extra-a.txt"
    extra_b = target / "extra-b.txt"
    backup_a = tmp_path / "backups" / "extra-a.txt"
    backup_b = tmp_path / "backups" / "extra-b.txt"
    backup_a.parent.mkdir()
    backup_a.write_text("old a\n")
    backup_b.write_text("old b\n")
    run = state_store.start_run(command="seed")
    state_store.record_asset_backup(
        run_id=run.id,
        target_dest=target,
        original_path=extra_a,
        backup_path=backup_a,
        content_hash=sha256_bytes(backup_a.read_bytes()),
        size=backup_a.stat().st_size,
        file_type="file",
    )
    state_store.record_asset_backup(
        run_id=run.id,
        target_dest=target,
        original_path=extra_b,
        backup_path=backup_b,
        content_hash=sha256_bytes(backup_b.read_bytes()),
        size=backup_b.stat().st_size,
        file_type="file",
    )
    backup_a.unlink()

    restored = Orchestrator(state_store).rollback(target)

    assert restored.status == "error"
    assert restored.changed is True
    assert "extra-a.txt" in (restored.error or "")
    assert not extra_a.exists()
    assert extra_b.read_text() == "old b\n"
    assert state_store.asset_backups(extra_a)[0].restored_at is None
    assert state_store.asset_backups(extra_b)[0].restored_at is not None


def test_asset_backup_restore_uses_latest_backup_per_original_path(tmp_path: Path, state_store) -> None:
    target = tmp_path / "system"
    extra = target / "extra.txt"
    older = tmp_path / "backups" / "older.txt"
    newer = tmp_path / "backups" / "newer.txt"
    older.parent.mkdir()
    older.write_text("older\n")
    newer.write_text("newer\n")
    run = state_store.start_run(command="seed")
    state_store.record_asset_backup(
        run_id=run.id,
        target_dest=target,
        original_path=extra,
        backup_path=older,
        content_hash=sha256_bytes(older.read_bytes()),
        size=older.stat().st_size,
        file_type="file",
    )
    state_store.record_asset_backup(
        run_id=run.id,
        target_dest=target,
        original_path=extra,
        backup_path=newer,
        content_hash=sha256_bytes(newer.read_bytes()),
        size=newer.stat().st_size,
        file_type="file",
    )

    restored = Orchestrator(state_store).rollback(target)

    assert restored.status == "restored"
    assert extra.read_text() == "newer\n"
    backups = state_store.asset_backups(extra)
    assert [backup.restored_at is not None for backup in backups] == [True, True]
    assert older.exists()
    assert not newer.exists()


def test_asset_backup_restore_conflicts_if_file_appears_after_preflight(tmp_path: Path, state_store) -> None:
    target = tmp_path / "system"
    extra_a = target / "extra-a.txt"
    extra_b = target / "extra-b.txt"
    backup_a = tmp_path / "backups" / "extra-a.txt"
    backup_b = tmp_path / "backups" / "extra-b.txt"
    backup_a.parent.mkdir()
    backup_a.write_text("old a\n")
    backup_b.write_text("old b\n")
    run = state_store.start_run(command="seed")
    state_store.record_asset_backup(
        run_id=run.id,
        target_dest=target,
        original_path=extra_a,
        backup_path=backup_a,
        content_hash=sha256_bytes(backup_a.read_bytes()),
        size=backup_a.stat().st_size,
        file_type="file",
    )
    state_store.record_asset_backup(
        run_id=run.id,
        target_dest=target,
        original_path=extra_b,
        backup_path=backup_b,
        content_hash=sha256_bytes(backup_b.read_bytes()),
        size=backup_b.stat().st_size,
        file_type="file",
    )
    calls = 0

    def create_manual_file_after_first_restore() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            extra_a.write_text("manual\n")

    restored = perform_rollback(target, state_store, require_current=create_manual_file_after_first_restore)

    assert restored.status == "conflict"
    assert restored.changed is True
    assert extra_a.read_text() == "manual\n"
    assert extra_b.read_text() == "old b\n"
    assert state_store.asset_backups(extra_a)[0].restored_at is None
    assert state_store.asset_backups(extra_b)[0].restored_at is not None


def test_asset_backup_fallback_restore_checks_current_lock(tmp_path: Path, state_store) -> None:
    target = tmp_path / "system"
    extra = target / "extra.txt"
    backup = tmp_path / "backups" / "extra.txt"
    backup.parent.mkdir()
    backup.write_text("old\n")
    run = state_store.start_run(command="seed")
    state_store.record_asset_backup(
        run_id=run.id,
        target_dest=target,
        original_path=extra,
        backup_path=backup,
        content_hash=sha256_bytes(backup.read_bytes()),
        size=backup.stat().st_size,
        file_type="file",
    )

    def lost_lock() -> None:
        raise RuntimeError("global lock was lost")

    with pytest.raises(RuntimeError, match="global lock was lost"):
        perform_rollback(target, state_store, require_current=lost_lock)

    assert not extra.exists()
    assert backup.exists()
    assert state_store.asset_backups(extra)[0].restored_at is None


def test_asset_backup_restore_checks_current_lock_before_each_file(tmp_path: Path, state_store) -> None:
    target = tmp_path / "system"
    extra_a = target / "extra-a.txt"
    extra_b = target / "extra-b.txt"
    backup_a = tmp_path / "backups" / "extra-a.txt"
    backup_b = tmp_path / "backups" / "extra-b.txt"
    backup_a.parent.mkdir()
    backup_a.write_text("old a\n")
    backup_b.write_text("old b\n")
    run = state_store.start_run(command="seed")
    state_store.record_asset_backup(
        run_id=run.id,
        target_dest=target,
        original_path=extra_a,
        backup_path=backup_a,
        content_hash=sha256_bytes(backup_a.read_bytes()),
        size=backup_a.stat().st_size,
        file_type="file",
    )
    state_store.record_asset_backup(
        run_id=run.id,
        target_dest=target,
        original_path=extra_b,
        backup_path=backup_b,
        content_hash=sha256_bytes(backup_b.read_bytes()),
        size=backup_b.stat().st_size,
        file_type="file",
    )
    calls = 0

    def lose_after_first_restore() -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("global lock was lost")

    with pytest.raises(RuntimeError, match="global lock was lost"):
        perform_rollback(target, state_store, require_current=lose_after_first_restore)

    assert extra_b.read_text() == "old b\n"
    assert not extra_a.exists()
    assert state_store.asset_backups(extra_b)[0].restored_at is not None
    assert state_store.asset_backups(extra_a)[0].restored_at is None


def test_asset_rollback_refuses_symlink_baseline_before_deleting_current_file(
    tmp_path: Path, state_store
) -> None:
    dest = tmp_path / "system" / "asset.txt"
    dest.parent.mkdir()
    dest.write_bytes(b"managed\n")
    run = state_store.start_run(command="seed")
    state_store.record_change_batch(
        run_id=run.id,
        path=dest,
        operations=[
            {
                "kind": "replace_file",
                "key": "file",
                "value": "managed\n",
                "before_value": None,
                "before_exists": True,
                "before_is_symlink": True,
                "before_symlink_target": "elsewhere.txt",
            }
        ],
        original_exists=True,
        format="asset",
    )

    rolled_back = Orchestrator(state_store).rollback(dest)

    assert rolled_back.status == "error"
    assert "symlink" in (rolled_back.error or "")
    assert dest.read_bytes() == b"managed\n"
    assert state_store.recovery_backups(dest) == []


def test_asset_rollback_records_permissions_for_rollback_of_rollback(tmp_path: Path, state_store) -> None:
    source = tmp_path / "repo" / "tool.sh"
    source.parent.mkdir()
    source.write_text("#!/bin/sh\n")
    source.chmod(0o755)
    dest = tmp_path / "system" / "tool.sh"
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/tool.sh'\ndest = 'system/tool.sh'\n")
    orchestrator = Orchestrator(state_store)
    assert orchestrator.run(spec_path)[0].status == "applied"

    rolled_back = orchestrator.rollback(dest)

    assert rolled_back.status == "rolled-back"
    latest = state_store.change_batches(dest)[-1]
    assert latest.operations[0].get("before_permissions") is not None


def test_orchestrator_asset_displacement_restores_extra_if_state_recording_fails(
    tmp_path: Path, state_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "managed.txt").write_text("managed\n")
    extra = tmp_path / "system" / "extra.txt"
    extra.parent.mkdir()
    extra.write_text("old\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[assets]]\nsource = 'repo/*.txt'\ndest = 'system'\nreplace = true\n")
    original_record_change_batch = state_store.record_change_batch

    def fail_displaced_change_batch(*args, **kwargs):
        if kwargs.get("format") == "asset-displaced":
            raise RuntimeError("simulated state failure")
        return original_record_change_batch(*args, **kwargs)

    monkeypatch.setattr(state_store, "record_change_batch", fail_displaced_change_batch)

    result = Orchestrator(state_store).run(spec_path)[0]

    assert result.status == "error"
    assert "simulated state failure" in (result.error or "")
    assert extra.read_text() == "old\n"
    backups = state_store.asset_backups(extra)
    assert len(backups) == 1
    assert backups[0].restored_at is not None


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
