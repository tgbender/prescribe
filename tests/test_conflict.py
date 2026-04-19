from pathlib import Path

from prescribe.core import FileFingerprint, detect_conflict


def test_detect_conflict_ignores_identical_content(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    fingerprint = FileFingerprint(
        path=path, hash_algo="sha256", content_hash=b"same", size=4
    )

    assert detect_conflict(fingerprint, fingerprint) is None
