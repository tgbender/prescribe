from pathlib import Path

import pytest

from prescribe.orchestrator import Orchestrator
from prescribe.spec import SpecError, SpecLoader


def test_line_target_accepts_inline_text(tmp_path: Path, state_store) -> None:
    profile = tmp_path / "profile.ps1"
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'profile.ps1'\n"
        "format = 'line'\n"
        "managed_block_id = 'aliases'\n"
        'text = """\n'
        "Set-Alias ll Get-ChildItem\n"
        "function gs { git status @args }\n"
        '"""\n'
    )

    result = Orchestrator(state_store).run(spec_path)[0]

    assert result.status == "applied"
    assert "Set-Alias ll Get-ChildItem" in profile.read_text()
    assert "function gs { git status @args }" in profile.read_text()


def test_line_target_accepts_text_from_relative_to_spec(tmp_path: Path, state_store) -> None:
    block = tmp_path / "blocks" / "aliases.ps1"
    block.parent.mkdir()
    block.write_text("Set-Alias g git\n")
    profile = tmp_path / "profile.ps1"
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'profile.ps1'\n"
        "format = 'line'\n"
        "managed_block_id = 'aliases'\n"
        "text_from = 'blocks/aliases.ps1'\n"
    )

    result = Orchestrator(state_store).run(spec_path)[0]

    assert result.status == "applied"
    assert "Set-Alias g git" in profile.read_text()


def test_line_text_from_reapply_after_rollback_original_clears_spaced_id_managed_drift(
    tmp_path: Path, monkeypatch, state_store
) -> None:
    monkeypatch.setenv("PRESCRIBE_DATA_DIR", str(tmp_path / "prescribe-data"))
    block = tmp_path / "generated" / "ssh-config-block.txt"
    block.parent.mkdir()
    block.write_text(
        "Host wsl-docker\n"
        "  HostName localhost\n"
        "  Port 2222\n"
        "  User test-user\n"
        "  IdentityFile ~/.ssh/id_ed25519\n"
        "  StrictHostKeyChecking accept-new\n"
    )
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text("Host existing\n  HostName example.test\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'ssh_config'\n"
        "format = 'line'\n"
        "managed_block_id = 'wsl2-setup ssh'\n"
        "text_from = 'generated/ssh-config-block.txt'\n"
    )

    orchestrator = Orchestrator(state_store)
    applied = orchestrator.run(spec_path)[0]
    assert applied.status == "applied", applied.error

    ssh_config.write_text(
        "# prescribe:begin wsl2-setup ssh\n"
        "Host wsl-docker\n"
        "  HostName 127.0.0.1\n"
        "  Port 2222\n"
        "  User test-user\n"
        "  IdentityFile ~/.ssh/id_ed25519\n"
        "  StrictHostKeyChecking accept-new\n"
        "# prescribe:end wsl2-setup ssh\n"
    )
    conflicted = orchestrator.run(spec_path, dry_run=True)[0]
    assert conflicted.status == "conflict"
    assert conflicted.conflict is not None
    assert conflicted.conflict.reason == "managed key 'wsl2-setup ssh' changed externally"

    rolled_back = orchestrator.rollback(ssh_config, original=True, conflict_resolver=lambda key: True)
    assert rolled_back.status == "rolled-back"
    assert "wsl-docker" not in ssh_config.read_text()

    reapplied = orchestrator.run(spec_path)[0]
    assert reapplied.status == "applied"

    status = orchestrator.run(spec_path, dry_run=True)[0]
    assert status.status == "dry-run"
    assert status.conflict is None
    assert status.changed is False


def test_line_text_from_reapply_after_rollback_original_clears_crlf_managed_drift(
    tmp_path: Path, monkeypatch, state_store
) -> None:
    monkeypatch.setenv("PRESCRIBE_DATA_DIR", str(tmp_path / "prescribe-data"))
    block = tmp_path / "generated" / "ssh-config-block.txt"
    block.parent.mkdir()
    block.write_bytes(
        b"Host wsl-docker\n"
        b"  HostName localhost\n"
        b"  Port 2222\n"
        b"  User test-user\n"
        b"  IdentityFile ~/.ssh/id_ed25519\n"
        b"  StrictHostKeyChecking accept-new\n"
    )
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_bytes(b"Host existing\r\n  HostName example.test\r\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'ssh_config'\n"
        "format = 'line'\n"
        "managed_block_id = 'wsl2-setup-ssh'\n"
        "text_from = 'generated/ssh-config-block.txt'\n"
    )

    orchestrator = Orchestrator(state_store)
    applied = orchestrator.run(spec_path)[0]
    assert applied.status == "applied", applied.error
    assert b"\r\n" in ssh_config.read_bytes()

    ssh_config.write_bytes(
        b"Host existing\r\n"
        b"  HostName example.test\r\n"
        b"# prescribe:begin wsl2-setup-ssh\r\n"
        b"Host wsl-docker\r\n"
        b"  HostName 127.0.0.1\r\n"
        b"  Port 2222\r\n"
        b"  User test-user\r\n"
        b"  IdentityFile ~/.ssh/id_ed25519\r\n"
        b"  StrictHostKeyChecking accept-new\r\n"
        b"# prescribe:end wsl2-setup-ssh\r\n"
    )
    conflicted = orchestrator.run(spec_path, dry_run=True)[0]
    assert conflicted.status == "conflict"
    assert conflicted.conflict is not None
    assert conflicted.conflict.reason == "managed key 'wsl2-setup-ssh' changed externally"

    rolled_back = orchestrator.rollback(ssh_config, original=True, conflict_resolver=lambda key: True)
    assert rolled_back.status == "rolled-back"
    assert b"\r\n" in ssh_config.read_bytes()
    assert b"Host existing\r\n" in ssh_config.read_bytes()

    reapplied = orchestrator.run(spec_path)[0]
    assert reapplied.status == "applied", reapplied.error

    status = orchestrator.run(spec_path, dry_run=True)[0]
    assert status.status == "dry-run"
    assert status.conflict is None
    assert status.changed is False


def test_line_target_rejects_text_and_lines_together(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\npath = 'profile.ps1'\nformat = 'line'\nmanaged_block_id = 'aliases'\nlines = ['a']\ntext = 'b'\n"
    )

    with pytest.raises(SpecError, match="either 'lines' or 'text'"):
        SpecLoader().load(spec_path)
