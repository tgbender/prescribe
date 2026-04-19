from pathlib import Path

from config_helper.adapters.line import LineAdapter


def test_line_adapter_updates_managed_block_and_preserves_unmanaged_text(
    make_text_file, line_sample: str, fake_root: Path
) -> None:
    source = make_text_file(".env", line_sample)

    adapter = LineAdapter()
    document = adapter.load(source)

    assert document.format == "line"
    assert document.root.block("managed") is not None

    document.root.ensure_block("managed", ["alpha=1", "beta=2"])

    target = fake_root / ".env.out"
    adapter.dump(document, target)

    assert target.read_text() == (
        "# header\n"
        "unmanaged before\n"
        "# config-helper:begin managed\n"
        "alpha=1\n"
        "beta=2\n"
        "# config-helper:end managed\n"
        "unmanaged after\n"
    )


def test_line_adapter_appends_missing_block(make_text_file, fake_root: Path) -> None:
    source = make_text_file(".gitignore", ".venv/\n__pycache__/\n")

    adapter = LineAdapter()
    document = adapter.load(source)
    document.root.ensure_block("managed", ["build/", "dist/"])

    target = fake_root / ".gitignore.out"
    adapter.dump(document, target)

    assert target.read_text() == (
        ".venv/\n"
        "__pycache__/\n"
        "# config-helper:begin managed\n"
        "build/\n"
        "dist/\n"
        "# config-helper:end managed\n"
    )


def test_line_adapter_removes_block(make_text_file, fake_root: Path) -> None:
    source = make_text_file(
        ".npmrc",
        "registry=https://example.invalid\n"
        "# config-helper:begin managed\n"
        "always-auth=true\n"
        "# config-helper:end managed\n"
        "save-exact=true\n",
    )

    adapter = LineAdapter()
    document = adapter.load(source)

    assert document.root.remove_block("managed") is True

    target = fake_root / ".npmrc.out"
    adapter.dump(document, target)

    assert target.read_text() == ("registry=https://example.invalid\nsave-exact=true\n")
