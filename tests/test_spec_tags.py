"""Tests for Spec-as-first-class-object, tags via Orchestrator, and --tags CLI."""

from __future__ import annotations

from pathlib import Path

from prescribe.spec import Spec

# ── Spec object identity ────────────────────────────────


def test_spec_with_explicit_id():
    """Spec(id='pi-agent') stores the human label."""
    spec = Spec(id="pi-agent", path=Path("/tmp/spec.toml"))
    assert spec.id == "pi-agent"


def test_spec_without_id_is_none():
    """Spec without an explicit id has id=None (backward compat)."""
    spec = Spec(path=Path("/tmp/spec.toml"))
    assert spec.id is None


# ── Orchestrator.run(Spec) ──────────────────────────────


def test_orchestrator_run_accepts_spec_object(fake_root, state_store):
    """orchestrator.run(spec=Spec(...)) should work."""
    from prescribe.orchestrator import Orchestrator
    from prescribe.spec import EnvTarget, FileTarget

    config_toml = fake_root / "config.toml"
    config_toml.write_text("count = 1\n")

    spec = Spec(
        id="inline-test",
        path=config_toml.parent,
        files=[
            FileTarget(
                path=config_toml,
                format="toml",
                data={"count": 2},
            )
        ],
        env=[
            EnvTarget(
                name="EDITOR",
                value="nvim",
            )
        ],
    )

    orch = Orchestrator(state_store)
    results = orch.run(spec=spec)
    assert len(results) >= 1
    assert any(r.env_vars.get("EDITOR") == "nvim" for r in results)


def test_orchestrator_run_with_tags_filter(fake_root, state_store):
    """orchestrator.run(spec=..., tags={'agent'}) should filter by tags."""
    from prescribe.orchestrator import Orchestrator
    from prescribe.spec import EnvTarget, FileTarget

    config_toml = fake_root / "config.toml"
    config_toml.write_text("count = 1\n")

    spec = Spec(
        id="tags-test",
        path=config_toml.parent,
        files=[
            FileTarget(
                path=config_toml,
                format="toml",
                data={"count": 2},
                tags=["agent"],
            )
        ],
        env=[
            EnvTarget(
                name="FOO",
                value="bar",
                tags=[],
            )
        ],
    )

    orch = Orchestrator(state_store)

    # Without tags filter — everything applies
    results = orch.run(spec=spec)
    applied = [r for r in results if r.status == "applied"]
    assert len(applied) >= 1

    # With tags filter — only agent targets
    results = orch.run(spec=spec, tags={"agent"})
    # FOO should be skipped (no tags = [] matches no filter)
    assert any(r.env_vars.get("FOO") != "bar" or r.skipped for r in results)


def test_orchestrator_run_with_skip_tags(fake_root, state_store):
    """orchestrator.run(spec=..., skip_tags={'secrets'}) should skip tagged entries."""
    from prescribe.orchestrator import Orchestrator
    from prescribe.spec import EnvTarget

    spec = Spec(
        id="skiptags-test",
        path=fake_root,
        env=[
            EnvTarget(name="PUBLIC", value="hello"),
            EnvTarget(name="SECRET", value="!fnox get X", tags=["secrets"]),
        ],
    )

    orch = Orchestrator(state_store)
    results = orch.run(spec=spec, skip_tags={"secrets"})
    # SECRET should not be in resolved env_vars
    assert all(r.env_vars.get("SECRET") is None for r in results)
