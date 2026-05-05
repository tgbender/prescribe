from dataclasses import dataclass, field
from typing import Literal

from prescribe.core.conflict import ConflictResult

OrchestrationStatus = Literal["applied", "noop", "dry-run", "conflict", "skipped", "error", "rolled-back", "restored"]


@dataclass(slots=True)
class OrchestrationResult:
    status: OrchestrationStatus
    applied: bool
    changed: bool
    conflict: ConflictResult | None = None
    skipped: bool = False
    dry_run: bool = False
    error: str | None = None
    env_vars: dict[str, str] = field(default_factory=dict)
    diff: str | None = None
    materialize_errors: list[str] = field(default_factory=list)
