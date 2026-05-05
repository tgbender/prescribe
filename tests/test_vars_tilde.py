"""Tests for [vars] section and ~ expansion in env entries."""

from __future__ import annotations

import os
from pathlib import Path

# ── [vars] section ───────────────────────────────────────


def test_vars_section_expands_in_env_values(tmp_path: Path):
    """[vars] variables expand in [[env]] values."""
    from prescribe.spec import SpecLoader

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[vars]\nBIN_DIR = '$HOME/.local/bin'\n\n[[env]]\nname = 'PATH'\nprepend = ['$BIN_DIR']\n")

    spec = SpecLoader().load(spec_path)
    # BIN_DIR should be resolved in the env target's prepend
    assert len(spec.env) == 1
    assert spec.env[0].prepend == [f"{os.environ['HOME']}/.local/bin"]


def test_vars_section_multiple_references(tmp_path: Path):
    """Variables can be referenced in multiple places."""
    from prescribe.spec import SpecLoader

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[vars]\n"
        "TOOLS = '$HOME/tools'\n"
        "\n"
        "[[env]]\n"
        "name = 'TOOLS'\n"
        "value = '$TOOLS'\n"
        "path_prepend = ['$TOOLS/bin']\n"
        "\n"
        "[[env]]\n"
        "name = 'PATH'\n"
        "append = ['$TOOLS/lib']\n"
    )

    spec = SpecLoader().load(spec_path)
    tools_home = f"{os.environ['HOME']}/tools"
    assert spec.env[0].value == tools_home
    assert spec.env[0].path_prepend == [f"{tools_home}/bin"]
    assert spec.env[1].append == [f"{tools_home}/lib"]


def test_vars_can_reference_env_vars(tmp_path: Path):
    """[vars] can reference $HOME and other process env vars."""
    from prescribe.spec import SpecLoader

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[vars]\nMY_HOME = '$HOME'\n\n[[env]]\nname = 'MY_HOME'\nvalue = '$MY_HOME'\n")

    spec = SpecLoader().load(spec_path)
    assert spec.env[0].value == os.environ["HOME"]


def test_vars_does_not_leak_between_sections(tmp_path: Path):
    """[vars] are resolved before target building, not stored on Spec."""
    from prescribe.spec import SpecLoader

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[vars]\nX = 'hello'\n\n[[env]]\nname = 'X'\nvalue = '$X'\n")

    spec = SpecLoader().load(spec_path)
    # Vars section is resolved — it shouldn't appear as a target
    assert not hasattr(spec, "vars") or spec.vars is None or len(spec.vars) == 0


# ── ~ expansion ──────────────────────────────────────────


def test_tilde_expands_in_env_value():
    """~ in env value expands to home directory via SpecLoader."""
    import tempfile

    from prescribe.spec import SpecLoader

    d = tempfile.mkdtemp()
    spec_path = Path(d) / "spec.toml"
    spec_path.write_text("[[env]]\nname = 'CARGO_HOME'\nvalue = '~/.cargo'\n")
    spec = SpecLoader().load(spec_path)
    assert spec.env[0].value == f"{os.environ['HOME']}/.cargo"


def test_tilde_expands_in_prepend():
    """~ in prepend entries expands to home directory via SpecLoader."""
    import tempfile

    from prescribe.spec import SpecLoader

    d = tempfile.mkdtemp()
    spec_path = Path(d) / "spec.toml"
    spec_path.write_text("[[env]]\nname = 'PATH'\nprepend = ['~/.local/bin']\n")
    spec = SpecLoader().load(spec_path)
    assert spec.env[0].prepend == [f"{os.environ['HOME']}/.local/bin"]


def test_tilde_expands_via_orchestrator(state_store):
    """Orchestrator.resolve_env expands ~ in values."""
    from prescribe.orchestrator import Orchestrator
    from prescribe.spec import EnvTarget, Spec

    spec = Spec(
        id="orchestrator-tilde",
        path=Path("/tmp"),
        env=[
            EnvTarget(name="CARGO_HOME", value="~/.cargo"),
            EnvTarget(name="PATH", prepend=["$CARGO_HOME/bin"]),
        ],
    )

    orch = Orchestrator(state_store)
    results = orch.run(spec=spec)
    resolved = results[0].env_vars

    home = os.environ["HOME"]
    assert resolved["CARGO_HOME"] == f"{home}/.cargo"
    paths = resolved["PATH"].split(os.pathsep)
    assert f"{home}/.cargo/bin" in paths
