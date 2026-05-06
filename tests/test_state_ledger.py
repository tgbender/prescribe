from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from prescribe.orchestrator import Orchestrator
from prescribe.spec import SpecLoader


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
    assert fresh.owner == "one"
    assert expired.owner == "two"


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
