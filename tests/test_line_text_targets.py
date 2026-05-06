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


def test_line_target_rejects_text_and_lines_together(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\npath = 'profile.ps1'\nformat = 'line'\nmanaged_block_id = 'aliases'\nlines = ['a']\ntext = 'b'\n"
    )

    with pytest.raises(SpecError, match="either 'lines' or 'text'"):
        SpecLoader().load(spec_path)
