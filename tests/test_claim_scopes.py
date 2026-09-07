from datetime import timedelta
from pathlib import Path

import pytest

from prescribe.claim_scopes import claims_overlap
from prescribe.claims import Claim, check_claim_conflicts_against, detect_internal_claim_conflicts, persist_claims
from prescribe.orchestrator import Orchestrator
from prescribe.spec import FileTarget, Spec
from prescribe.state import StateStore


def claim(kind: str, subject: Path, address: str, owner: str) -> Claim:
    return Claim(kind, str(subject), address, owner, None, None)


@pytest.mark.parametrize(
    ("left_kind", "left_path", "left_address", "right_kind", "right_path", "right_address", "overlap"),
    [
        ("file", "config", "a", "file", "config", "a.b", True),
        ("file", "config", "a.b", "file", "config", "a.c", False),
        ("file", "config", "a", "file", "config", "ab", False),
        ("asset", "config", "file", "file", "config", "a", True),
        ("asset", "tree", "tree", "file", "tree/config", "a", True),
        ("asset", "tree", "tree", "asset", "tree/subtree", "tree", True),
        ("asset", "tree", "tree", "file", "tree-other/config", "a", False),
        ("line", "profile", "env", "shell", "profile", "env", True),
        ("line", "profile", "one", "shell", "profile", "two", False),
    ],
)
def test_claim_overlap_is_symmetric_and_used_by_preflight(
    tmp_path: Path,
    left_kind: str,
    left_path: str,
    left_address: str,
    right_kind: str,
    right_path: str,
    right_address: str,
    overlap: bool,
) -> None:
    left = claim(left_kind, tmp_path / left_path, left_address, "owner-a")
    right = claim(right_kind, tmp_path / right_path, right_address, "owner-b")
    assert claims_overlap(left, right) is overlap
    assert claims_overlap(right, left) is overlap
    assert bool(detect_internal_claim_conflicts([left, right])) is overlap
    store = StateStore(tmp_path / "state.db")
    store.initialize()
    assert persist_claims(store, [left]) == []
    check = check_claim_conflicts_against(store.claims(), [right], resolver=None)
    assert bool(check.conflicts) is overlap


def test_parent_takeover_retires_child_claim_without_releasing_siblings(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    orchestrator = Orchestrator(store)
    config = tmp_path / "config.toml"
    first = Spec(
        path=tmp_path / "first.toml",
        files=[FileTarget(config, "toml", data={"service.token": "secret", "other.value": "keep"})],
    )
    second = Spec(path=tmp_path / "second.toml", files=[FileTarget(config, "toml", delete=["service"])])
    assert orchestrator.run(first)[0].status == "applied"
    assert orchestrator.run(second, dry_run=True)[0].status == "error"
    assert orchestrator.run(second, claim_resolver=lambda _: True)[0].status == "applied"
    owners = {record.address: record.owner_id for record in store.claims()}
    assert owners == {"service": f"{second.path}#files[0]", "other.value": f"{first.path}#files[0]"}
    assert orchestrator.run(second)[0].status == "noop"
    assert orchestrator.run(first)[0].status == "error"
    assert "keep" in config.read_text(encoding="utf-8")


def test_overlapping_reservation_requires_previous_lease_to_expire(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    store.initialize()
    first = store.reserve_claim(
        target_type="asset",
        subject=str(tmp_path / "tree"),
        address="tree",
        owner_id="first",
        run_id=None,
        token="first-token",
        ttl=timedelta(minutes=5),
    )
    incoming = dict(
        target_type="file",
        subject=str(tmp_path / "tree" / "config"),
        address="value",
        owner_id="second",
        run_id=None,
        token="second-token",
    )
    blocked = store.reserve_claim(**incoming)
    assert blocked == first
    assert store.claim_reservations() == [first]
    acquired = store.reserve_claim(**incoming, now=first.expires_at + timedelta(seconds=1))
    assert acquired.owner_id == "second"


def test_promotion_requires_all_overlapping_owners_to_approve(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    store.initialize()
    children = [claim("file", tmp_path / "config", f"a.{leaf}", leaf) for leaf in ("one", "two")]
    assert persist_claims(store, children) == []
    before = store.claims()
    parent = claim("file", tmp_path / "config", "a", "parent")
    conflicts = persist_claims(store, [parent], resolver=lambda conflict: conflict.existing_owner == "one")
    assert len(conflicts) == 1
    assert conflicts[0].existing_owner == "two"
    assert store.claims() == before
