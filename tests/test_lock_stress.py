from __future__ import annotations

import multiprocessing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Empty
from typing import Any

import pytest

from prescribe.state import StateStore


pytestmark = pytest.mark.stress_lock


def _acquire_lock_worker(
    state_path: str,
    owner: str,
    start: Any,
    results: Any,
    *,
    now_iso: str | None = None,
) -> None:
    try:
        start.wait(timeout=5)
        now = None if now_iso is None else datetime.fromisoformat(now_iso)
        lock = StateStore(Path(state_path)).acquire_lock(
            "global",
            owner=owner,
            ttl=timedelta(seconds=30),
            now=now,
        )
        results.put(
            {
                "owner": owner,
                "lock_owner": lock.owner,
                "token": lock.token,
                "acquired": lock.acquired,
            }
        )
    except BaseException as exc:
        results.put({"owner": owner, "error": repr(exc)})


def _collect_results(results: Any, count: int) -> list[dict[str, Any]]:
    collected = []
    for _ in range(count):
        try:
            collected.append(results.get(timeout=10))
        except Empty:
            break
    return collected


def test_multiprocess_fresh_lock_contention_has_single_winner(tmp_path: Path) -> None:
    state_path = tmp_path / "state.db"
    StateStore(state_path).initialize()
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    process_count = 8
    processes = [
        ctx.Process(
            target=_acquire_lock_worker,
            args=(str(state_path), f"owner-{index}", start, results),
        )
        for index in range(process_count)
    ]

    for process in processes:
        process.start()
    start.set()
    collected = _collect_results(results, process_count)
    for process in processes:
        process.join(timeout=10)

    assert len(collected) == process_count
    assert [item for item in collected if "error" in item] == []
    winners = [item for item in collected if item["acquired"]]
    losers = [item for item in collected if not item["acquired"]]
    assert len(winners) == 1
    assert len(losers) == process_count - 1
    assert {item["lock_owner"] for item in losers} == {winners[0]["lock_owner"]}
    assert all(process.exitcode == 0 for process in processes)


def test_multiprocess_expired_lock_takeover_has_single_winner(tmp_path: Path) -> None:
    state_path = tmp_path / "state.db"
    store = StateStore(state_path)
    store.initialize()
    store.acquire_lock(
        "global",
        owner="expired",
        ttl=timedelta(seconds=1),
        now=datetime.now(UTC) - timedelta(minutes=2),
    )
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    process_count = 8
    now_iso = datetime.now(UTC).isoformat()
    processes = [
        ctx.Process(
            target=_acquire_lock_worker,
            args=(str(state_path), f"owner-{index}", start, results),
            kwargs={"now_iso": now_iso},
        )
        for index in range(process_count)
    ]

    for process in processes:
        process.start()
    start.set()
    collected = _collect_results(results, process_count)
    for process in processes:
        process.join(timeout=10)

    assert len(collected) == process_count
    assert [item for item in collected if "error" in item] == []
    winners = [item for item in collected if item["acquired"]]
    losers = [item for item in collected if not item["acquired"]]
    assert len(winners) == 1
    assert len(losers) == process_count - 1
    assert winners[0]["lock_owner"] != "expired"
    assert {item["lock_owner"] for item in losers} == {winners[0]["lock_owner"]}
    assert all(process.exitcode == 0 for process in processes)
