"""Regression coverage for ownership, rollback, recovery, and shell PATH safety.

Tests use real temporary files and SQLite databases, without mocks.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from prescribe.orchestrator import Orchestrator
from prescribe.spec import AssetTarget, EnvTarget, FileTarget, ShellTarget, Spec
from prescribe.state import StateStore


@pytest.fixture
def review_store(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path / "state.db")
    store.initialize()
    return store


@pytest.mark.parametrize("overlap", ["parent-delete", "whole-file-asset"])
def test_overlapping_claim_is_rejected_before_writing(tmp_path: Path, review_store: StateStore, overlap: str) -> None:
    target = tmp_path / "config.toml"
    owner = Spec(
        path=tmp_path / "owner.toml",
        files=[FileTarget(target, "toml", data={"service.token": "owned"})],
    )
    intruder = Spec(path=tmp_path / "intruder.toml")
    if overlap == "parent-delete":
        intruder.files = [FileTarget(target, "toml", delete=["service"])]
    else:
        source = tmp_path / "replacement.toml"
        source.write_text("unrelated = true\n", encoding="utf-8")
        intruder.assets = [AssetTarget(str(source), target)]

    orchestrator = Orchestrator(review_store)
    assert orchestrator.run(owner)[0].status == "applied"
    original_bytes = target.read_bytes()
    original_claims = review_store.claims()
    original_batches = review_store.change_batches(target)

    result = orchestrator.run(intruder)[0]

    assert result.status == "error", "Overlapping ownership must require an explicit takeover"
    assert "claim conflict" in (result.error or "")
    assert target.read_bytes() == original_bytes
    assert review_store.claims() == original_claims
    assert review_store.change_batches(target) == original_batches


@pytest.mark.parametrize(
    ("initial", "delete_key", "expected"),
    [
        ({"editor.fontSize": 14, "keep": True}, "editor.fontSize", {"keep": True}),
        (
            {"editor.overrides": {"fontSize": 14, "tabSize": 2}, "keep": True},
            "editor.overrides.fontSize",
            {"editor.overrides": {"tabSize": 2}, "keep": True},
        ),
        (
            {"editor": {"fontSize": 14, "tabSize": 2}, "keep": True},
            "editor.fontSize",
            {"editor": {"tabSize": 2}, "keep": True},
        ),
    ],
    ids=["flat-key", "flat-prefix", "nested-control"],
)
def test_jsonc_dotted_deletion_applies_and_rolls_back(
    tmp_path: Path,
    review_store: StateStore,
    initial: dict[str, object],
    delete_key: str,
    expected: dict[str, object],
) -> None:
    target = tmp_path / "settings.json"
    target.write_text(json.dumps(initial), encoding="utf-8")
    spec = Spec(
        path=tmp_path / "spec.toml",
        files=[FileTarget(target, "jsonc", delete=[delete_key])],
    )
    orchestrator = Orchestrator(review_store)

    result = orchestrator.run(spec)[0]

    assert result.status == "applied", "Existing dotted properties must be deleted, not reported as noop"
    assert json.loads(target.read_text(encoding="utf-8")) == expected
    assert orchestrator.run(spec)[0].status == "noop"
    batches = review_store.change_batches(target)
    assert len(batches) == 1
    assert batches[0].operations[0]["before_value"] == 14
    assert orchestrator.rollback(target).status == "rolled-back"
    assert json.loads(target.read_text(encoding="utf-8")) == initial


def test_mirror_rollback_restores_all_identical_displaced_files(tmp_path: Path, review_store: StateStore) -> None:
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    originals: dict[Path, bytes] = {}
    for folder in ("a", "b", "c"):
        (source / folder).mkdir(parents=True)
        (source / folder / "keep.txt").write_text("keep\n", encoding="utf-8")
        (dest / folder).mkdir(parents=True)
        extra = dest / folder / "extra.txt"
        originals[extra] = b"same contents\n"
        extra.write_bytes(originals[extra])
        # Copied/extracted trees can preserve identical mtimes and basenames.
        os.utime(extra, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    spec = Spec(
        path=tmp_path / "spec.toml",
        assets=[AssetTarget(str(source / "**" / "*.txt"), dest, mode="mirror", replace=True)],
    )
    orchestrator = Orchestrator(review_store)
    assert orchestrator.run(spec)[0].status == "applied"
    assert all(not path.exists() for path in originals)
    backups = review_store.unrestored_asset_backups_for_target(dest)
    assert {backup.original_path for backup in backups} == set(originals)
    assert all(backup.backup_path.read_bytes() == originals[backup.original_path] for backup in backups)

    result = orchestrator.rollback(dest)

    # Assert the public recovery contract, allowing either unique backups or
    # correctly implemented shared immutable backup storage.
    assert result.status == "restored", result.error
    assert {path: path.read_bytes() for path in originals} == originals
    assert review_store.unrestored_asset_backups_for_target(dest) == []
    assert all((dest / folder / "keep.txt").read_text(encoding="utf-8") == "keep\n" for folder in ("a", "b", "c"))
    assert orchestrator.rollback(dest).status == "noop"


def test_rollback_preserves_both_parent_creation_and_literal_key_metadata(
    tmp_path: Path, review_store: StateStore
) -> None:
    target = tmp_path / "settings.json"
    initial = {"editor.fontSize": 14, "keep": True}
    target.write_text(json.dumps(initial), encoding="utf-8")
    spec = Spec(
        path=tmp_path / "spec.toml",
        files=[FileTarget(target, "jsonc", data={"new.section.value": 42}, delete=["editor.fontSize"])],
    )
    orchestrator = Orchestrator(review_store)

    assert orchestrator.run(spec)[0].status == "applied"
    assert json.loads(target.read_text(encoding="utf-8")) == {"keep": True, "new": {"section": {"value": 42}}}
    operations = review_store.change_batches(target)[0].operations
    assert next(op for op in operations if op["kind"] == "set")["created_parent_keys"] == ["new", "new.section"]
    assert next(op for op in operations if op["kind"] == "delete")["before_key_parts"] == ["editor.fontSize"]

    # Use a fresh store to verify that rollback reads both kinds of persisted
    # metadata, restores the literal key, and removes only newly created parents.
    reopened = Orchestrator(StateStore(review_store.path))
    assert reopened.rollback(target).status == "rolled-back"
    assert json.loads(target.read_text(encoding="utf-8")) == initial


def test_recovery_preserves_attempts_and_reservations_of_live_writer(tmp_path: Path, review_store: StateStore) -> None:
    owner = "live-writer"
    lock = review_store.acquire_lock("global", owner=owner, ttl=timedelta(minutes=5))
    assert lock.acquired and lock.token is not None
    run = review_store.start_run(spec_hash=b"review-live-writer")
    subject = str(tmp_path / "config.toml")
    review_store.record_target_attempt(
        run_id=run.id,
        target_type="file",
        target_id="files[0]",
        subject=subject,
        address="key",
        owner_id=owner,
        phase="attempting",
    )
    review_store.reserve_claim(
        target_type="file",
        subject=subject,
        address="key",
        owner_id=owner,
        run_id=run.id,
        token=lock.token,
        ttl=timedelta(minutes=5),
    )
    attempts = review_store.unfinished_target_attempts()
    reservations = review_store.claim_reservations()
    # A separate store observes the real persisted lease, without timing races
    # or sharing a SQLite connection between threads.
    recovery = Orchestrator(StateStore(review_store.path))
    try:
        result = recovery.recover_interrupted(force=True)

        assert result.status == "error", "Recovery must refuse to clear a writer that still owns the lease"
        assert result.changed is False
        assert review_store.unfinished_target_attempts() == attempts
        assert review_store.claim_reservations() == reservations
        assert review_store.lock_is_current("global", owner=owner, token=lock.token)
    finally:
        review_store.release_lock("global", owner=owner, token=lock.token)

    # Refusing live writers must not prevent operator recovery after release.
    assert recovery.recover_interrupted(force=True).status == "applied"
    assert review_store.unfinished_target_attempts() == []
    assert {record.status for record in review_store.claim_reservations()} == {"expired"}


@pytest.mark.parametrize("source_count", [1, 2], ids=["first-source", "repeated-source"])
@pytest.mark.parametrize(
    "shell_type",
    [
        pytest.param("pwsh", marks=pytest.mark.skipif(os.name != "nt", reason="requires Windows")),
        pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="requires POSIX")),
    ],
)
def test_shell_path_additions_preserve_runtime_path(
    tmp_path: Path, review_store: StateStore, shell_type: str, source_count: int
) -> None:
    if shell_type == "pwsh":
        executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    else:
        executable = shutil.which("bash")
    if executable is None:
        pytest.skip(f"{shell_type} executable is unavailable")
    prepend = str(tmp_path / "prepend tools")
    append = str(tmp_path / "append tools")
    inherited = [str(tmp_path / "runtime bin"), str(tmp_path / "other bin")]
    profile = tmp_path / ("profile.ps1" if shell_type == "pwsh" else "profile.sh")
    spec = Spec(
        path=tmp_path / "spec.toml",
        env=[EnvTarget(name="PATH", prepend=[prepend], append=[append])],
        shell=[ShellTarget(profile, "paths", shells=[shell_type])],
    )
    orchestrator = Orchestrator(review_store)
    assert orchestrator.run(spec)[0].status == "applied"
    assert orchestrator.run(spec)[0].status == "noop"

    env = dict(os.environ)
    env.update(
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
        XDG_CONFIG_HOME=str(tmp_path / ".config"),
        PATH=os.pathsep.join(inherited),
    )
    if shell_type == "pwsh":
        driver = tmp_path / "driver.ps1"
        driver.write_text(
            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new()\n"
            + ". (Join-Path $PSScriptRoot 'profile.ps1')\n" * source_count
            + "[Console]::Write($env:PATH)\n",
            encoding="utf-8",
        )
        command = [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(driver)]
    else:
        # Pass the observed path as an argument; do not interpolate it as code.
        command = [
            executable,
            "--noprofile",
            "--norc",
            "-c",
            '. "$1"\n' * source_count + 'printf "%s" "$PATH"',
            "review-shell",
            str(profile),
        ]
        env.pop("BASH_ENV", None)
        env.pop("ENV", None)

    output = tmp_path / "shell-output.txt"
    with output.open("wb") as handle:
        completed = subprocess.run(command, env=env, stdout=handle, stderr=subprocess.STDOUT, timeout=15)
    shell_output = output.read_text(encoding="utf-8-sig")
    assert completed.returncode == 0, shell_output

    assert shell_output.split(os.pathsep) == [prepend, *inherited, append], (
        "Prepend/append must preserve the runtime PATH in order, including after repeated profile sourcing"
    )
