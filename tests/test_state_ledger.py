from __future__ import annotations

import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from prescribe.claims import compute_claims, detect_internal_claim_conflicts
from prescribe.adapters.toml import TomlAdapter
from prescribe.orchestrator import Orchestrator
from prescribe.spec import AssetTarget, EnvTarget, FileTarget, ShellTarget, Spec, SpecLoader
from prescribe.state import StateStore


def test_run_lock_refuses_fresh_owner_and_allows_expired(memory_state_store) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)

    first = memory_state_store.acquire_lock("global", owner="one", ttl=timedelta(minutes=15), now=now)
    fresh = memory_state_store.acquire_lock("global", owner="two", ttl=timedelta(minutes=15), now=now)
    expired = memory_state_store.acquire_lock(
        "global",
        owner="two",
        ttl=timedelta(minutes=15),
        now=now + timedelta(minutes=16),
    )

    assert first.owner == "one"
    assert first.token is not None
    assert fresh.owner == "one"
    assert fresh.token == first.token
    assert expired.owner == "two"
    assert expired.token is not None
    assert expired.token != first.token


def test_run_lock_release_and_heartbeat_require_current_token(memory_state_store) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    first = memory_state_store.acquire_lock("global", owner="one", ttl=timedelta(minutes=1), now=now)
    assert first.token is not None

    assert memory_state_store.lock_is_current("global", owner="one", token=first.token, now=now)
    assert not memory_state_store.lock_is_current("global", owner="one", token="wrong", now=now)
    assert memory_state_store.heartbeat_lock("global", owner="one", token="wrong", now=now) is None
    memory_state_store.release_lock("global", owner="one", token="wrong")
    assert memory_state_store.lock_is_current("global", owner="one", token=first.token, now=now)

    refreshed = memory_state_store.heartbeat_lock(
        "global",
        owner="one",
        token=first.token,
        ttl=timedelta(minutes=5),
        now=now + timedelta(seconds=30),
    )
    assert refreshed is not None
    assert refreshed.expires_at == now + timedelta(minutes=5, seconds=30)

    memory_state_store.release_lock("global", owner="one", token=first.token)
    assert memory_state_store.active_lock("global") is None


def test_long_apply_heartbeats_global_lock(tmp_path: Path) -> None:
    state_path = tmp_path / "state.db"
    store = StateStore(state_path)
    started = threading.Event()
    release = threading.Event()

    def slow_materialize(*, env_vars, dry_run=False) -> None:
        started.set()
        assert release.wait(timeout=2)

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[env]]\nname = 'EDITOR'\nvalue = 'nvim'\nmaterialize = true\n")
    orch = Orchestrator(
        store,
        _materialize_fn=slow_materialize,
        lock_ttl=timedelta(milliseconds=500),
        lock_heartbeat_interval=timedelta(milliseconds=100),
    )
    result_holder: dict[str, list[Any]] = {}

    def run_apply() -> None:
        result_holder["results"] = orch.run(spec_path)

    worker = threading.Thread(target=run_apply)
    worker.start()
    assert started.wait(timeout=2)
    time.sleep(0.75)

    competing = StateStore(state_path).acquire_lock("global", owner="other", ttl=timedelta(milliseconds=500))

    release.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert competing.owner != "other"
    assert result_holder["results"][0].status == "applied"


def test_managed_claim_requires_take_for_new_owner(memory_state_store) -> None:
    first = memory_state_store.upsert_claim(
        target_type="file",
        subject="config.toml",
        address="count",
        owner_id="spec-a#files[0]",
    )
    conflict = memory_state_store.upsert_claim(
        target_type="file",
        subject="config.toml",
        address="count",
        owner_id="spec-b#files[0]",
    )
    taken = memory_state_store.upsert_claim(
        target_type="file",
        subject="config.toml",
        address="count",
        owner_id="spec-b#files[0]",
        take=True,
    )

    assert first.owner_id == "spec-a#files[0]"
    assert conflict.owner_id == "spec-a#files[0]"
    assert taken.owner_id == "spec-b#files[0]"


def test_claim_reservation_blocks_until_ttl_expires(memory_state_store) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)

    first = memory_state_store.reserve_claim(
        target_type="file",
        subject="config.toml",
        address="count",
        owner_id="spec-a#files[0]",
        run_id=None,
        token="token-a",
        ttl=timedelta(minutes=1),
        now=now,
    )
    blocked = memory_state_store.reserve_claim(
        target_type="file",
        subject="config.toml",
        address="count",
        owner_id="spec-b#files[0]",
        run_id=None,
        token="token-b",
        ttl=timedelta(minutes=1),
        now=now + timedelta(seconds=30),
    )
    stolen = memory_state_store.reserve_claim(
        target_type="file",
        subject="config.toml",
        address="count",
        owner_id="spec-b#files[0]",
        run_id=None,
        token="token-b",
        ttl=timedelta(minutes=1),
        now=now + timedelta(minutes=2),
    )

    assert first.token == "token-a"
    assert blocked.token == "token-a"
    assert blocked.owner_id == "spec-a#files[0]"
    assert stolen.token == "token-b"
    assert stolen.owner_id == "spec-b#files[0]"


def test_apply_records_run_and_target_ledger(tmp_path: Path, memory_state_store) -> None:
    config = tmp_path / "config.toml"
    config.write_text("count = 1\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    result = Orchestrator(memory_state_store).run(spec_path)
    run_id = memory_state_store.latest_run_id()

    assert result[0].status == "applied"
    assert run_id is not None
    targets = memory_state_store.target_runs(run_id)
    assert len(targets) == 1
    assert targets[0].target_type == "file"
    assert targets[0].status == "applied"
    attempts = memory_state_store.target_attempts(run_id)
    assert len(attempts) == 1
    assert attempts[0].phase == "succeeded"


def test_claims_follow_active_tag_filter(tmp_path: Path, memory_state_store) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "tags = ['base']\n"
        "[files.data]\n"
        "count = 1\n"
        "\n"
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "priority = 10\n"
        "tags = ['work']\n"
        "[files.data]\n"
        "count = 2\n"
    )
    spec = SpecLoader().load(spec_path)

    result = Orchestrator(memory_state_store).run(spec, tags={"base"})

    assert result[0].status == "applied"
    claims = memory_state_store.claims()
    assert len(claims) == 1
    assert claims[0].owner_id.endswith("#files[0]")


def test_failed_target_does_not_leave_durable_claim(tmp_path: Path, memory_state_store) -> None:
    config = tmp_path / "config.toml"
    config.write_text("count = [\n")
    failing_spec = tmp_path / "failing.toml"
    failing_spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    orchestrator = Orchestrator(memory_state_store)
    failed = orchestrator.run(failing_spec)

    assert failed[0].status == "error"
    assert memory_state_store.claims() == []
    run_id = memory_state_store.latest_run_id()
    assert run_id is not None
    attempts = memory_state_store.target_attempts(run_id)
    assert len(attempts) == 1
    assert attempts[0].phase == "failed"
    assert memory_state_store.claim_reservations()[0].status == "failed"

    config.write_text("count = 1\n")
    valid_spec = tmp_path / "valid.toml"
    valid_spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 3\n")

    applied = orchestrator.run(valid_spec)

    assert applied[0].status == "applied"
    claims = memory_state_store.claims()
    assert len(claims) == 1
    assert claims[0].owner_id.endswith("valid.toml#files[0]")


def test_unexpired_claim_reservation_blocks_apply(tmp_path: Path, memory_state_store) -> None:
    config = tmp_path / "config.toml"
    config.write_text("count = 1\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")
    spec = SpecLoader().load(spec_path)
    claim = compute_claims(spec)[0]
    memory_state_store.reserve_claim(
        target_type=claim.target_type,
        subject=claim.subject,
        address=claim.address,
        owner_id="other-spec#files[0]",
        run_id=None,
        token="other-token",
        ttl=timedelta(minutes=5),
    )

    result = Orchestrator(memory_state_store).run(spec_path)

    assert result[0].status == "error"
    assert "claim reservation conflict" in (result[0].error or "")
    assert memory_state_store.claims() == []
    assert TomlAdapter().load(config).root["count"] == 1


def test_durable_claim_blocks_even_without_active_reservation(tmp_path: Path, memory_state_store) -> None:
    config = tmp_path / "config.toml"
    config.write_text("count = 1\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")
    spec = SpecLoader().load(spec_path)
    claim = compute_claims(spec)[0]
    memory_state_store.upsert_claim(
        target_type=claim.target_type,
        subject=claim.subject,
        address=claim.address,
        owner_id="other-spec#files[0]",
    )

    result = Orchestrator(memory_state_store).run(spec_path)

    assert result[0].status == "error"
    assert "claim conflict" in (result[0].error or "")
    assert TomlAdapter().load(config).root["count"] == 1


def test_durable_claim_blocks_dry_run_without_writing(tmp_path: Path, memory_state_store) -> None:
    config = tmp_path / "config.toml"
    config.write_text("count = 1\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")
    spec = SpecLoader().load(spec_path)
    claim = compute_claims(spec)[0]
    memory_state_store.upsert_claim(
        target_type=claim.target_type,
        subject=claim.subject,
        address=claim.address,
        owner_id="other-spec#files[0]",
    )

    result = Orchestrator(memory_state_store).run(spec_path, dry_run=True)

    assert result[0].status == "error"
    assert "claim conflict" in (result[0].error or "")
    assert TomlAdapter().load(config).root["count"] == 1


def test_materialize_failure_fails_apply(tmp_path: Path, memory_state_store) -> None:
    def failing_materialize(*, env_vars, dry_run=False) -> None:
        raise RuntimeError("materialize exploded")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[env]]\nname = 'EDITOR'\nvalue = 'nvim'\nmaterialize = true\n")

    results = Orchestrator(memory_state_store, _materialize_fn=failing_materialize).run(spec_path)

    assert results[0].status == "error"
    assert "materialize exploded" in (results[0].error or "")
    assert results[0].materialize_errors == ["materialize exploded"]


class SnapshotFailingStore(StateStore):
    def record_snapshot(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("snapshot recording failed")


def test_post_write_state_failure_leaves_attempt_unfinished_for_recovery(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("count = 1\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")
    store = SnapshotFailingStore(tmp_path / "state.db")

    results = Orchestrator(store).run(spec_path)

    assert results[0].status == "error"
    assert "snapshot recording failed" in (results[0].error or "")
    assert "count = 2" in config.read_text()
    attempts = store.unfinished_target_attempts()
    assert len(attempts) == 1
    assert attempts[0].phase == "attempting"


def test_completed_attempts_do_not_block_later_apply(tmp_path: Path, memory_state_store) -> None:
    config = tmp_path / "config.toml"
    config.write_text("count = 1\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    orchestrator = Orchestrator(memory_state_store)
    first = orchestrator.run(spec_path)
    second = orchestrator.run(spec_path)

    assert first[0].status == "applied"
    assert second[0].status == "noop"
    attempts = memory_state_store.target_attempts()
    assert attempts
    assert {attempt.phase for attempt in attempts} == {"succeeded"}


def test_recover_interrupted_is_noop_when_state_is_clean(memory_state_store) -> None:
    result = Orchestrator(memory_state_store).recover_interrupted()

    assert result.status == "noop"
    assert result.applied is False
    assert result.changed is False


def test_env_claim_is_recorded_when_env_shares_run_with_file(tmp_path: Path, memory_state_store) -> None:
    config = tmp_path / "config.toml"
    config.write_text("count = 1\n")
    spec = Spec(
        path=tmp_path / "spec.toml",
        env=[EnvTarget(name="EDITOR", value="nvim")],
        files=[FileTarget(path=config, format="toml", data={"count": 2})],
    )

    result = Orchestrator(memory_state_store).run(spec)

    assert result[0].status == "applied"
    claims = memory_state_store.claims()
    assert {(claim.target_type, claim.address) for claim in claims} == {("env", "value"), ("file", "count")}


def test_apply_metadata_failure_leaves_recoverable_attempt_without_claim(
    tmp_path: Path, memory_state_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    config.write_text("count = 1\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    def fail_record_target_run(*args, **kwargs):
        raise RuntimeError("simulated ledger failure")

    monkeypatch.setattr(memory_state_store, "record_target_run", fail_record_target_run)

    with pytest.raises(RuntimeError, match="simulated ledger failure"):
        Orchestrator(memory_state_store).run(spec_path)

    run_id = memory_state_store.latest_run_id()
    assert run_id is not None
    assert memory_state_store.claims() == []
    assert [record.path for record in memory_state_store.list_managed()] == [config.resolve()]
    attempts = memory_state_store.target_attempts(run_id)
    assert len(attempts) == 1
    assert attempts[0].phase == "attempting"
    reservations = memory_state_store.claim_reservations()
    assert len(reservations) == 1
    assert reservations[0].status == "reserved"
    monkeypatch.undo()

    blocked = Orchestrator(memory_state_store).run(spec_path)
    assert blocked[0].status == "error"
    assert "interrupted prescribe operation detected" in (blocked[0].error or "")

    dry_recovery = Orchestrator(memory_state_store).recover_interrupted(dry_run=True)
    assert dry_recovery.status == "dry-run"
    assert "interrupted prescribe operation detected" in (dry_recovery.error or "")
    assert memory_state_store.target_attempts(run_id)[0].phase == "attempting"

    refused_recovery = Orchestrator(memory_state_store).recover_interrupted()
    assert refused_recovery.status == "error"
    assert memory_state_store.target_attempts(run_id)[0].phase == "attempting"

    recovered = Orchestrator(memory_state_store).recover_interrupted(force=True)
    assert recovered.status == "applied"
    assert memory_state_store.target_attempts(run_id)[0].phase == "interrupted"
    assert memory_state_store.claim_reservations()[0].status == "expired"

    reapplied = Orchestrator(memory_state_store).run(spec_path)
    assert reapplied[0].status in {"applied", "noop"}
    assert len(memory_state_store.claims()) == 1


def test_cli_recovery_prompt_can_clear_interrupted_state(
    tmp_path: Path, memory_state_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    config.write_text("count = 1\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    def fail_record_target_run(*args, **kwargs):
        raise RuntimeError("simulated ledger failure")

    monkeypatch.setattr(memory_state_store, "record_target_run", fail_record_target_run)
    with pytest.raises(RuntimeError, match="simulated ledger failure"):
        Orchestrator(memory_state_store).run(spec_path)
    monkeypatch.undo()

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("typer.confirm", lambda _prompt: True)

    from prescribe.cli import _prompt_recover_interrupted

    assert _prompt_recover_interrupted(Orchestrator(memory_state_store), output_json=False)
    assert {attempt.phase for attempt in memory_state_store.target_attempts()} == {"interrupted"}
    assert {reservation.status for reservation in memory_state_store.claim_reservations()} == {"expired"}

    reapplied = Orchestrator(memory_state_store).run(spec_path)
    assert reapplied[0].status in {"applied", "noop"}


def test_cli_recovery_prompt_decline_leaves_interrupted_state(
    tmp_path: Path, memory_state_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    config.write_text("count = 1\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    def fail_record_target_run(*args, **kwargs):
        raise RuntimeError("simulated ledger failure")

    monkeypatch.setattr(memory_state_store, "record_target_run", fail_record_target_run)
    with pytest.raises(RuntimeError, match="simulated ledger failure"):
        Orchestrator(memory_state_store).run(spec_path)
    monkeypatch.undo()

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("typer.confirm", lambda _prompt: False)

    from prescribe.cli import _prompt_recover_interrupted

    assert not _prompt_recover_interrupted(Orchestrator(memory_state_store), output_json=False)
    assert {attempt.phase for attempt in memory_state_store.target_attempts()} == {"attempting"}
    assert {reservation.status for reservation in memory_state_store.claim_reservations()} == {"reserved"}


def test_windows_file_claim_subjects_are_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    spec = Spec(
        path=Path("spec.toml"),
        files=[
            FileTarget(path=Path(r"C:\Users\Travis\.config\Tool.toml"), format="toml", data={"a": 1}),
            FileTarget(path=Path(r"c:\users\travis\.config\tool.toml"), format="toml", data={"a": 2}),
        ],
    )

    conflicts = detect_internal_claim_conflicts(compute_claims(spec))

    assert len(conflicts) == 1


def test_path_claims_reject_portable_case_collisions() -> None:
    spec = Spec(
        path=Path("spec.toml"),
        files=[
            FileTarget(path=Path("Foo.toml"), format="toml", data={"a": 1}),
            FileTarget(path=Path("foo.toml"), format="toml", data={"b": 2}),
        ],
    )

    conflicts = detect_internal_claim_conflicts(compute_claims(spec))

    assert len(conflicts) == 1
    assert conflicts[0].message is not None
    assert "portable path collision" in conflicts[0].message
    assert "Foo.toml" in conflicts[0].message
    assert "foo.toml" in conflicts[0].message


def test_path_claims_reject_portable_collisions_across_target_types() -> None:
    spec = Spec(
        path=Path("spec.toml"),
        shell=[ShellTarget(path=Path("Profile.ps1"), managed_block_id="profile")],
        assets=[AssetTarget(source="profile.ps1", dest=Path("profile.ps1"))],
    )

    conflicts = detect_internal_claim_conflicts(compute_claims(spec))

    assert len(conflicts) == 1
    assert conflicts[0].message is not None
    assert "portable path collision" in conflicts[0].message


def test_windows_env_claim_subjects_are_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    from prescribe.spec import EnvTarget

    monkeypatch.setattr("sys.platform", "win32")
    spec = Spec(path=Path("spec.toml"), env=[EnvTarget(name="Path", value="a"), EnvTarget(name="PATH", value="b")])

    conflicts = detect_internal_claim_conflicts(compute_claims(spec))

    assert len(conflicts) == 1
