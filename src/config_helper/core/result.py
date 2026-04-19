from __future__ import annotations

from dataclasses import dataclass

from config_helper.core.conflict import ConflictResult


@dataclass(slots=True)
class OrchestrationResult:
    status: str
    applied: bool
    changed: bool
    conflict: ConflictResult | None = None
    skipped: bool = False
    dry_run: bool = False
    error: str | None = None
