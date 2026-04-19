from pathlib import Path

from prescribe.adapters.line import LineAdapter
from prescribe.adapters.toml import TomlAdapter
from prescribe.core import DesiredState, Planner, detect_conflict, FileFingerprint


def test_planner_detects_missing_and_different_toml_keys(make_text_file) -> None:
    source = make_text_file("config.toml", "title = 'hello'\ncount = 1\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(
            path=source,
            format="toml",
            data={"title": "hello", "count": 2, "extra": True},
        ),
    )

    assert plan.changed is True
    assert [op.kind for op in plan.operations] == ["update", "set"]
    assert plan.operations[0].key == "count"
    assert plan.operations[1].key == "extra"


def test_planner_detects_matching_line_block(make_text_file) -> None:
    source = make_text_file(
        ".env",
        "# prescribe:begin managed\nalpha=1\nbeta=2\n# prescribe:end managed\n",
    )

    document = LineAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(
            path=source,
            format="line",
            managed_block_id="managed",
            lines=["alpha=1", "beta=2"],
        ),
    )

    assert plan.changed is False
    assert plan.operations == []


def test_conflict_detection_flags_hash_changes(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    baseline = FileFingerprint(
        path=path, hash_algo="sha256", content_hash=b"abc", size=10
    )
    current = FileFingerprint(
        path=path, hash_algo="sha256", content_hash=b"xyz", size=10
    )

    conflict = detect_conflict(baseline, current)

    assert conflict is not None
    assert conflict.reason == "content changed"
