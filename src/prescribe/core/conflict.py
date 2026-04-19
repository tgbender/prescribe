from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class FileFingerprint:
    path: Path
    hash_algo: str
    content_hash: bytes
    size: int
    mtime_ns: int | None = None


@dataclass(slots=True)
class ConflictResult:
    path: Path
    changed: bool
    reason: str
    baseline_fingerprint: FileFingerprint | None
    current_fingerprint: FileFingerprint

    @property
    def baseline_hash(self) -> bytes | None:
        if self.baseline_fingerprint is None:
            return None
        return self.baseline_fingerprint.content_hash

    @property
    def current_hash(self) -> bytes:
        return self.current_fingerprint.content_hash


def file_fingerprint(
    path: Path,
    hash_algo: str,
    content_hash: bytes,
    size: int,
    mtime_ns: int | None = None,
) -> FileFingerprint:
    return FileFingerprint(
        path=path,
        hash_algo=hash_algo,
        content_hash=content_hash,
        size=size,
        mtime_ns=mtime_ns,
    )


def detect_conflict(
    baseline: FileFingerprint | None, current: FileFingerprint
) -> ConflictResult | None:
    if baseline is None:
        return None
    if baseline.hash_algo != current.hash_algo:
        return ConflictResult(
            path=current.path,
            changed=True,
            reason="hash algorithm changed",
            baseline_fingerprint=baseline,
            current_fingerprint=current,
        )
    if baseline.content_hash != current.content_hash:
        return ConflictResult(
            path=current.path,
            changed=True,
            reason="content changed",
            baseline_fingerprint=baseline,
            current_fingerprint=current,
        )
    return None
