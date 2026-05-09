from __future__ import annotations

import ntpath
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from prescribe.spec import Spec
from prescribe.state import ManagedClaimRecord, StateStore


@dataclass(frozen=True, slots=True)
class Claim:
    target_type: str
    subject: str
    address: str
    owner_id: str
    spec_path: str | None
    target_id: str | None
    raw_subject: str | None = None
    portable_subject: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimConflict:
    claim: Claim
    existing_owner: str
    existing: ManagedClaimRecord | None = None
    message: str | None = None


ClaimResolver = Callable[[ClaimConflict], bool]
ClaimKey = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class ClaimCheckResult:
    conflicts: list[ClaimConflict]
    take_keys: set[ClaimKey]


def compute_claims(spec: Spec, *, include: Callable[[object], bool] | None = None) -> list[Claim]:
    spec_path = str(spec.path) if spec.path else None
    claims: list[Claim] = []
    for index, file_target in enumerate(spec.files):
        if include is not None and not include(file_target):
            continue
        owner = _owner_id(spec_path, "files", index)
        if file_target.format == "line" and file_target.managed_block_id:
            subject = str(file_target.path)
            claims.append(
                Claim(
                    target_type="line",
                    subject=_claim_subject("line", subject),
                    address=file_target.managed_block_id,
                    owner_id=owner,
                    spec_path=spec_path,
                    target_id=f"files[{index}]",
                    raw_subject=subject,
                    portable_subject=_portable_path_subject(subject),
                )
            )
            continue
        subject = str(file_target.path)
        for key in sorted({*_mapping_claim_keys(file_target.data), *file_target.delete}):
            claims.append(
                Claim(
                    target_type="file",
                    subject=_claim_subject("file", subject),
                    address=key,
                    owner_id=owner,
                    spec_path=spec_path,
                    target_id=f"files[{index}]",
                    raw_subject=subject,
                    portable_subject=_portable_path_subject(subject),
                )
            )

    for index, env_target in enumerate(spec.env):
        if include is not None and not include(env_target):
            continue
        claims.append(
            Claim(
                target_type="env",
                subject=_claim_subject("env", env_target.name),
                address="value",
                owner_id=f"{spec_path or '<memory>'}#env:{env_target.name}",
                spec_path=spec_path,
                target_id=f"env[{index}]",
            )
        )

    for index, shell_target in enumerate(spec.shell):
        if include is not None and not include(shell_target):
            continue
        subject = str(shell_target.path)
        claims.append(
            Claim(
                target_type="shell",
                subject=_claim_subject("shell", subject),
                address=shell_target.managed_block_id,
                owner_id=_owner_id(spec_path, "shell", index),
                spec_path=spec_path,
                target_id=f"shell[{index}]",
                raw_subject=subject,
                portable_subject=_portable_path_subject(subject),
            )
        )

    for index, asset_target in enumerate(spec.assets):
        if include is not None and not include(asset_target):
            continue
        address = "tree" if asset_target.mode == "mirror" else "file"
        subject = str(asset_target.dest)
        claims.append(
            Claim(
                target_type="asset",
                subject=_claim_subject("asset", subject),
                address=address,
                owner_id=_owner_id(spec_path, "assets", index),
                spec_path=spec_path,
                target_id=f"assets[{index}]",
                raw_subject=subject,
                portable_subject=_portable_path_subject(subject),
            )
        )

    return claims


def detect_internal_claim_conflicts(claims: list[Claim]) -> list[ClaimConflict]:
    seen: dict[tuple[str, str, str], Claim] = {}
    conflicts: list[ClaimConflict] = []
    for claim in claims:
        key = (claim.target_type, claim.subject, claim.address)
        existing = seen.get(key)
        if existing is not None and existing.owner_id != claim.owner_id:
            conflicts.append(ClaimConflict(claim=claim, existing_owner=existing.owner_id))
            continue
        seen[key] = claim
    conflicts.extend(_detect_portable_path_collisions(claims))
    return conflicts


def persist_claims(
    store: StateStore,
    claims: list[Claim],
    *,
    resolver: Any = None,
    connection: sqlite3.Connection | None = None,
) -> list[ClaimConflict]:
    conflicts: list[ClaimConflict] = []
    for claim in claims:
        existing = store.upsert_claim(
            target_type=claim.target_type,
            subject=claim.subject,
            address=claim.address,
            owner_id=claim.owner_id,
            spec_path=claim.spec_path,
            target_id=claim.target_id,
            connection=connection,
        )
        if existing.owner_id == claim.owner_id:
            continue
        conflict = ClaimConflict(claim=claim, existing_owner=existing.owner_id, existing=existing)
        should_take = bool(resolver(conflict)) if resolver is not None else False
        if should_take:
            store.upsert_claim(
                target_type=claim.target_type,
                subject=claim.subject,
                address=claim.address,
                owner_id=claim.owner_id,
                spec_path=claim.spec_path,
                target_id=claim.target_id,
                take=True,
                connection=connection,
            )
            continue
        conflicts.append(conflict)
    return conflicts


def check_claim_conflicts(
    store: StateStore,
    claims: list[Claim],
    *,
    resolver: ClaimResolver | None,
) -> ClaimCheckResult:
    return check_claim_conflicts_against(store.claims(), claims, resolver=resolver)


def check_claim_conflicts_against(
    existing_claims: list[ManagedClaimRecord],
    claims: list[Claim],
    *,
    resolver: ClaimResolver | None,
) -> ClaimCheckResult:
    existing = {claim_key(claim): claim for claim in existing_claims}
    conflicts: list[ClaimConflict] = []
    take_keys: set[ClaimKey] = set()
    for claim in claims:
        key = claim_key(claim)
        current = existing.get(key)
        if current is None or current.owner_id == claim.owner_id:
            continue
        conflict = ClaimConflict(claim=claim, existing_owner=current.owner_id, existing=current)
        if resolver is not None and resolver(conflict):
            take_keys.add(key)
            continue
        conflicts.append(conflict)
    return ClaimCheckResult(conflicts=conflicts, take_keys=take_keys)


def claim_key(claim: Claim | ManagedClaimRecord) -> ClaimKey:
    return (claim.target_type, claim.subject, claim.address)


def _owner_id(spec_path: str | None, section: str, index: int) -> str:
    prefix = spec_path or "<memory>"
    return f"{prefix}#{section}[{index}]"


def _claim_subject(target_type: str, subject: str) -> str:
    if sys.platform != "win32":
        return subject
    if target_type in {"file", "line", "shell", "asset"}:
        return ntpath.normcase(subject)
    if target_type == "env":
        return subject.casefold()
    return subject


def _portable_path_subject(subject: str) -> str:
    return ntpath.normcase(ntpath.normpath(subject))


def _detect_portable_path_collisions(claims: list[Claim]) -> list[ClaimConflict]:
    seen: dict[str, Claim] = {}
    conflicts: list[ClaimConflict] = []
    for claim in claims:
        if claim.portable_subject is None:
            continue
        existing = seen.get(claim.portable_subject)
        if existing is None:
            seen[claim.portable_subject] = claim
            continue
        if existing.raw_subject == claim.raw_subject:
            continue
        if (
            existing.target_type == claim.target_type
            and existing.subject == claim.subject
            and existing.address == claim.address
        ):
            continue
        conflicts.append(
            ClaimConflict(
                claim=claim,
                existing_owner=existing.owner_id,
                message=(
                    "portable path collision: "
                    f"{existing.raw_subject} and {claim.raw_subject} refer to the same case-insensitive path"
                ),
            )
        )
    return conflicts


def _mapping_claim_keys(value: dict[str, Any], prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, inner in value.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(inner, dict):
            keys.update(_mapping_claim_keys(inner, dotted))
        else:
            keys.add(dotted)
    return keys
