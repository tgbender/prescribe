"""Tests for env var expansion ordering in _resolve_env."""

from __future__ import annotations

import os
from pathlib import Path


def test_env_var_expands_using_resolved_vars(state_store, monkeypatch):
    """$CARGO_HOME/bin should expand using CARGO_HOME from the same spec."""
    from prescribe.orchestrator import Orchestrator
    from prescribe.spec import EnvTarget, Spec

    monkeypatch.setenv("CARGO_HOME", "/fake/from/env")

    spec = Spec(
        id="expand-test",
        path=Path("/tmp"),
        env=[
            EnvTarget(name="CARGO_HOME", value="$HOME/.cargo"),
            EnvTarget(name="PATH", prepend=["$CARGO_HOME/bin"]),
        ],
    )

    orch = Orchestrator(state_store)
    results = orch.run(spec=spec)
    resolved = results[0].env_vars

    # CARGO_HOME should be set from the spec, not the process env
    assert resolved["CARGO_HOME"] == f"{os.environ['HOME']}/.cargo"

    # PATH prepend should expand $CARGO_HOME using the RESOLVED value, not the process env
    paths = resolved["PATH"].split(os.pathsep)
    assert f"{os.environ['HOME']}/.cargo/bin" in paths
    # Should NOT contain the fake process env value
    assert "/fake/from/env/bin" not in paths


def test_env_var_expands_in_path_prepend(state_store, monkeypatch):
    """path_prepend entries should expand using the resolved env."""
    from prescribe.orchestrator import Orchestrator
    from prescribe.spec import EnvTarget, Spec

    spec = Spec(
        id="path-prepend-test",
        path=Path("/tmp"),
        env=[
            EnvTarget(name="TOOLS", value="$HOME/tools"),
            EnvTarget(name="TOOLS", path_prepend=["$TOOLS/bin"]),
        ],
    )

    orch = Orchestrator(state_store)
    results = orch.run(spec=spec)
    resolved = results[0].env_vars

    paths = resolved["PATH"].split(os.pathsep)
    assert f"{os.environ['HOME']}/tools/bin" in paths


def test_env_var_expands_in_append_using_resolved(state_store, monkeypatch):
    """append entries should expand using the resolved env."""
    from prescribe.orchestrator import Orchestrator
    from prescribe.spec import EnvTarget, Spec

    monkeypatch.setenv("APP_DIR", "/wrong/path")

    spec = Spec(
        id="append-test",
        path=Path("/tmp"),
        env=[
            EnvTarget(name="APP_DIR", value="$HOME/apps"),
            EnvTarget(name="PATH", append=["$APP_DIR/bin"]),
        ],
    )

    orch = Orchestrator(state_store)
    results = orch.run(spec=spec)
    resolved = results[0].env_vars

    paths = resolved["PATH"].split(os.pathsep)
    assert f"{os.environ['HOME']}/apps/bin" in paths
    assert "/wrong/path/bin" not in paths
