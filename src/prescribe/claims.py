from __future__ import annotations

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


@dataclass(frozen=True, slots=True)
class ClaimConflict:
    claim: Claim
    existing_owner: str
    existing: ManagedClaimRecord | None = None


ClaimResolver = Callable[[ClaimConflict], bool]


def compute_claims(spec: Spec, *, include: Callable[[object], bool] | None = None) -> list[Claim]:
    spec_path = str(spec.path) if spec.path else None
    claims: list[Claim] = []
    for index, file_target in enumerate(spec.files):
        if include is not None and not include(file_target):
            continue
        owner = _owner_id(spec_path, "files", index)
        if file_target.format == "line" and file_target.managed_block_id:
            claims.append(
                Claim(
                    target_type="line",
                    subject=str(file_target.path),
                    address=file_target.managed_block_id,
                    owner_id=owner,
                    spec_path=spec_path,
                    target_id=f"files[{index}]",
                )
            )
            continue
        for key in sorted({*_mapping_claim_keys(file_target.data), *file_target.delete}):
            claims.append(
                Claim(
                    target_type="file",
                    subject=str(file_target.path),
                    address=key,
                    owner_id=owner,
                    spec_path=spec_path,
                    target_id=f"files[{index}]",
                )
            )

    for index, env_target in enumerate(spec.env):
        if include is not None and not include(env_target):
            continue
        claims.append(
            Claim(
                target_type="env",
                subject=env_target.name,
                address="value",
                owner_id=f"{spec_path or '<memory>'}#env:{env_target.name}",
                spec_path=spec_path,
                target_id=f"env[{index}]",
            )
        )

    for index, shell_target in enumerate(spec.shell):
        if include is not None and not include(shell_target):
            continue
        claims.append(
            Claim(
                target_type="shell",
                subject=str(shell_target.path),
                address=shell_target.managed_block_id,
                owner_id=_owner_id(spec_path, "shell", index),
                spec_path=spec_path,
                target_id=f"shell[{index}]",
            )
        )

    for index, asset_target in enumerate(spec.assets):
        if include is not None and not include(asset_target):
            continue
        address = "tree" if asset_target.mode == "mirror" else "file"
        claims.append(
            Claim(
                target_type="asset",
                subject=str(asset_target.dest),
                address=address,
                owner_id=_owner_id(spec_path, "assets", index),
                spec_path=spec_path,
                target_id=f"assets[{index}]",
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
    return conflicts


def persist_claims(
    store: StateStore,
    claims: list[Claim],
    *,
    resolver: Any = None,
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
            )
            continue
        conflicts.append(conflict)
    return conflicts


def _owner_id(spec_path: str | None, section: str, index: int) -> str:
    prefix = spec_path or "<memory>"
    return f"{prefix}#{section}[{index}]"


def _mapping_claim_keys(value: dict[str, Any], prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, inner in value.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(inner, dict):
            keys.update(_mapping_claim_keys(inner, dotted))
        else:
            keys.add(dotted)
    return keys
