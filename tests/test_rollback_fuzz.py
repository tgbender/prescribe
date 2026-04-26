from __future__ import annotations

import random
from pathlib import Path

import pytest

from prescribe.adapters import adapter_for_path
from prescribe.orchestrator import Orchestrator

FORMATS = ("toml", "yaml", "json5", "jsonc", "line")
MAPPING_VARIANTS = 3
LINE_VARIANTS = 2


@pytest.mark.parametrize("seed", range(36))
def test_rollback_fuzz_preserves_unmanaged_changes_and_restores_managed_state(
    make_text_file, fake_root: Path, state_store, seed: int
) -> None:
    rng = random.Random(seed)
    fmt = FORMATS[seed % len(FORMATS)]
    variant = rng.randrange(LINE_VARIANTS if fmt == "line" else MAPPING_VARIANTS)
    case_dir = fake_root / f"case_{seed:03d}" / f"grp_{rng.randrange(3)}" / f"sub_{rng.randrange(5)}"
    case_dir.mkdir(parents=True, exist_ok=True)

    target_name = _target_name_for_format(fmt)
    target_path = case_dir / target_name
    spec_path = case_dir / "spec.toml"
    use_absolute_path = seed % 2 == 1
    spec_target_path = str(target_path) if use_absolute_path else target_path.relative_to(spec_path.parent).as_posix()
    round_count = 2 + rng.randrange(4)

    original_text = _initial_text(fmt, seed, variant)
    target_path.write_text(original_text, encoding="utf-8")

    expected_text = original_text
    expected_semantic = _expected_semantic_state(fmt, seed, variant)
    chain: list[str] = [
        f"seed={seed} fmt={fmt} variant={variant} target={target_path} "
        f"absolute_path={use_absolute_path} rounds={round_count}"
    ]

    orchestrator = Orchestrator(state_store)

    for round_index in range(round_count):
        spec_text = _spec_text(fmt, spec_target_path, seed, round_index, variant)
        spec_path.write_text(spec_text, encoding="utf-8")
        chain.append(f"apply[{round_index}] spec={spec_text!r}")
        result = orchestrator.run(spec_path, tool_version="fuzz")
        assert result[0].status == "applied", _failure_report(chain, target_path)

        if round_index == 0:
            manual_comment = f"manual-note-{seed}-{variant}-{round_index}"
            manual_external = f"manual-{seed}-{variant}-{round_index}"
            serialized_text = target_path.read_text(encoding="utf-8")
            expected_text = _apply_manual_edit(
                fmt,
                variant,
                serialized_text,
                manual_comment=manual_comment,
                manual_external=manual_external,
            )
            _apply_manual_edit_to_file(
                fmt,
                variant,
                target_path,
                manual_comment=manual_comment,
                manual_external=manual_external,
            )
            expected_semantic = _apply_manual_semantic_edit(
                fmt, variant, expected_semantic, manual_external=manual_external
            )
            chain.append(f"manual[{round_index}] external={manual_external} comment={manual_comment}")

    rolled_back = orchestrator.rollback(target_path)
    chain.append(f"rollback status={rolled_back.status} applied={rolled_back.applied} changed={rolled_back.changed}")
    assert rolled_back.status == "rolled-back", _failure_report(chain, target_path)

    final_document = adapter_for_path(target_path).load(target_path)
    final_text = target_path.read_text(encoding="utf-8")
    try:
        if fmt == "line":
            assert final_document.root.block(_line_profile(variant)["managed_block_id"]) is not None
            assert [
                line.rstrip("\r\n")
                for line in final_document.root.block(_line_profile(variant)["managed_block_id"]).lines
            ] == expected_semantic["managed_lines"]
            assert expected_semantic[_line_profile(variant)["tail_key"]] in final_text
        else:
            assert final_document.root == expected_semantic
            assert _mapping_manual_path(variant)[-1] in final_text
            if fmt != "json5":
                assert f"manual-note-{seed}-{variant}-0" in final_text
                assert "seed" in final_text
    except AssertionError as exc:
        raise AssertionError(
            _failure_report(
                chain,
                target_path,
                expected_text=expected_text,
                actual_text=final_text,
                expected_semantic=expected_semantic,
                actual_semantic=final_document.root,
            )
        ) from exc


def _target_name_for_format(fmt: str) -> str:
    if fmt == "toml":
        return "config.toml"
    if fmt == "yaml":
        return "config.yaml"
    if fmt == "json5":
        return "config.json5"
    if fmt == "jsonc":
        return "config.jsonc"
    if fmt == "line":
        return ".env"
    raise ValueError(fmt)


def _initial_text(fmt: str, seed: int, variant: int) -> str:
    if fmt == "line":
        return _line_initial_text(seed, variant)
    if variant == 0:
        return _mapping_flat_initial_text(fmt, seed)
    if variant == 1:
        return _mapping_nested_initial_text(fmt, seed)
    if variant == 2:
        return _mapping_list_initial_text(fmt, seed)
    raise ValueError(variant)


def _mapping_flat_initial_text(fmt: str, seed: int) -> str:
    if fmt == "toml":
        return (
            f"# seed {seed}\n"
            "title = 'alpha'\n"
            "count = 1\n"
            "temporary = 'remove-me'\n"
            "external = 'keep'\n"
            "nestedEnabled = true\n"
        )
    if fmt == "yaml":
        return f"# seed {seed}\ntitle: alpha\ncount: 1\ntemporary: remove-me\nexternal: keep\nnestedEnabled: true\n"
    if fmt == "json5":
        return (
            f"// seed {seed}\n"
            "{\n"
            "  title: 'alpha',\n"
            "  count: 1,\n"
            "  temporary: 'remove-me',\n"
            "  external: 'keep',\n"
            "  nestedEnabled: true,\n"
            "}\n"
        )
    if fmt == "jsonc":
        return (
            "{\n"
            f"  // seed {seed}\n"
            '  "title": "alpha",\n'
            '  "count": 1,\n'
            '  "temporary": "remove-me",\n'
            '  "external": "keep",\n'
            '  "nestedEnabled": true\n'
            "}\n"
        )
    raise ValueError(fmt)


def _mapping_nested_initial_text(fmt: str, seed: int) -> str:
    if fmt == "toml":
        return (
            f"# seed {seed}\n"
            "title = 'alpha'\n"
            "temporary = 'remove-me'\n"
            "[service]\n"
            "enabled = true\n"
            "retries = 1\n"
            "mode = 'steady'\n"
            "[meta]\n"
            "owner = 'keep'\n"
            "external = 'keep'\n"
            "tags = ['one', 'two']\n"
        )
    if fmt == "yaml":
        return (
            f"# seed {seed}\n"
            "title: alpha\n"
            "temporary: remove-me\n"
            "service:\n"
            "  enabled: true\n"
            "  retries: 1\n"
            "  mode: steady\n"
            "meta:\n"
            "  owner: keep\n"
            "  external: keep\n"
            "  tags:\n"
            "    - one\n"
            "    - two\n"
        )
    if fmt == "json5":
        return (
            f"// seed {seed}\n"
            "{\n"
            "  title: 'alpha',\n"
            "  temporary: 'remove-me',\n"
            "  service: { enabled: true, retries: 1, mode: 'steady' },\n"
            "  meta: { owner: 'keep', external: 'keep', tags: ['one', 'two'] },\n"
            "}\n"
        )
    if fmt == "jsonc":
        return (
            "{\n"
            f"  // seed {seed}\n"
            '  "title": "alpha",\n'
            '  "temporary": "remove-me",\n'
            '  "service": {\n'
            '    "enabled": true,\n'
            '    "retries": 1,\n'
            '    "mode": "steady"\n'
            "  },\n"
            '  "meta": {\n'
            '    "owner": "keep",\n'
            '    "external": "keep",\n'
            '    "tags": ["one", "two"]\n'
            "  }\n"
            "}\n"
        )
    raise ValueError(fmt)


def _mapping_list_initial_text(fmt: str, seed: int) -> str:
    if fmt == "toml":
        return (
            f"# seed {seed}\n"
            "title = 'alpha'\n"
            "count = 1\n"
            "temporary = 'remove-me'\n"
            "[settings]\n"
            "enabled = true\n"
            "retries = 1\n"
            "zones = ['a', 'b']\n"
            "features = ['core', 'extra']\n"
            "[meta]\n"
            "external = 'keep'\n"
        )
    if fmt == "yaml":
        return (
            f"# seed {seed}\n"
            "title: alpha\n"
            "count: 1\n"
            "temporary: remove-me\n"
            "settings:\n"
            "  enabled: true\n"
            "  retries: 1\n"
            "  zones:\n"
            "    - a\n"
            "    - b\n"
            "  features:\n"
            "    - core\n"
            "    - extra\n"
            "meta:\n"
            "  external: keep\n"
        )
    if fmt == "json5":
        return (
            f"// seed {seed}\n"
            "{\n"
            "  title: 'alpha',\n"
            "  count: 1,\n"
            "  temporary: 'remove-me',\n"
            "  settings: { enabled: true, retries: 1, zones: ['a', 'b'], features: ['core', 'extra'] },\n"
            "  meta: { external: 'keep' },\n"
            "}\n"
        )
    if fmt == "jsonc":
        return (
            "{\n"
            f"  // seed {seed}\n"
            '  "title": "alpha",\n'
            '  "count": 1,\n'
            '  "temporary": "remove-me",\n'
            '  "settings": {\n'
            '    "enabled": true,\n'
            '    "retries": 1,\n'
            '    "zones": ["a", "b"],\n'
            '    "features": ["core", "extra"]\n'
            "  },\n"
            '  "meta": {\n'
            '    "external": "keep"\n'
            "  }\n"
            "}\n"
        )
    raise ValueError(fmt)


def _line_initial_text(seed: int, variant: int) -> str:
    if variant == 0:
        return (
            f"# seed {seed}\n"
            "header=keep\n"
            "# prescribe:begin managed\n"
            "alpha=1\n"
            "beta=2\n"
            "# prescribe:end managed\n"
            "footer=keep\n"
        )
    if variant == 1:
        return (
            f"# seed {seed}\n"
            "prefix=keep\n"
            "# prescribe:begin managed-alt\n"
            "alpha=1\n"
            "beta=2\n"
            "gamma=3\n"
            "# prescribe:end managed-alt\n"
            "postscript=keep\n"
        )
    raise ValueError(variant)


def _expected_semantic_state(fmt: str, seed: int, variant: int):
    if fmt == "line":
        profile = _line_profile(variant)
        return {
            "managed_lines": profile["managed_lines"],
            profile["tail_key"]: "keep",
            profile["head_key"]: "keep",
        }
    if variant == 0:
        return {
            "title": "alpha",
            "count": 1,
            "temporary": "remove-me",
            "external": "keep",
            "nestedEnabled": True,
        }
    if variant == 1:
        return {
            "title": "alpha",
            "temporary": "remove-me",
            "service": {"enabled": True, "retries": 1, "mode": "steady"},
            "meta": {"owner": "keep", "external": "keep", "tags": ["one", "two"]},
        }
    if variant == 2:
        return {
            "title": "alpha",
            "count": 1,
            "temporary": "remove-me",
            "settings": {
                "enabled": True,
                "retries": 1,
                "zones": ["a", "b"],
                "features": ["core", "extra"],
            },
            "meta": {"external": "keep"},
        }
    raise ValueError(variant)


def _spec_text(fmt: str, path_text: str, seed: int, round_index: int, variant: int) -> str:
    if fmt == "line":
        profile = _line_profile(variant)
        lines = [
            "[[targets]]",
            f"path = '{path_text}'",
            "format = 'line'",
            f"managed_block_id = '{profile['managed_block_id']}'",
            "lines = [",
        ]
        for entry in profile["managed_lines_for_round"](round_index):
            lines.append(f"  '{entry}',")
        lines.append("]")
        return "\n".join(lines) + "\n"

    managed = _mapping_managed_state(fmt, seed, round_index, variant)
    delete = ["temporary"] if round_index == 0 else []
    return _mapping_spec(path_text, fmt, managed, delete)


def _mapping_managed_state(fmt: str, seed: int, round_index: int, variant: int) -> dict[str, object]:
    if variant == 0:
        return {
            "count": round_index + 2,
            "nestedEnabled": round_index % 2 == 0,
            f"round_{round_index}": f"seed-{seed}",
        }
    if variant == 1:
        return {
            "service": {"enabled": round_index % 2 == 0, "retries": round_index + 2},
            "tags": ["one", "two", f"round-{round_index}"],
            f"round_{round_index}": f"seed-{seed}",
        }
    if variant == 2:
        return {
            "count": round_index + 2,
            "settings": {
                "enabled": round_index % 2 == 0,
                "retries": round_index + 1,
                "zones": ["a", f"zone-{round_index}"],
                "features": ["core", f"feature-{round_index}"],
            },
            f"round_{round_index}": f"seed-{seed}",
        }
    raise ValueError(variant)


def _mapping_spec(path_text: str, fmt: str, managed: dict[str, object], delete: list[str]) -> str:
    lines = [
        "[[targets]]",
        f"path = '{path_text}'",
        f"format = '{fmt}'",
    ]
    if delete:
        delete_items = ", ".join(f"'{item}'" for item in delete)
        lines.append(f"delete = [{delete_items}]")
    lines.append("[targets.data]")
    lines.extend(_toml_table_lines(managed))
    return "\n".join(lines) + "\n"


def _toml_table_lines(value: dict[str, object], prefix: str = "") -> list[str]:
    lines: list[str] = []
    for key, item in value.items():
        dotted_key = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            lines.extend(_toml_table_lines(item, dotted_key))
        else:
            lines.append(f"{dotted_key} = {_toml_literal(item)}")
    return lines


def _toml_literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_literal(item) for item in value) + "]"
    return f"'{value}'"


def _apply_manual_edit(fmt: str, variant: int, text: str, *, manual_comment: str, manual_external: str) -> str:
    if fmt == "toml":
        return (
            "# " + manual_comment + "\n" + _replace_first(text, "external = 'keep'", f"external = '{manual_external}'")
        )
    if fmt == "yaml":
        return "# " + manual_comment + "\n" + _replace_first(text, "external: keep", f"external: {manual_external}")
    if fmt == "json5":
        return (
            "// "
            + manual_comment
            + "\n"
            + _replace_first(text, '"external": "keep"', f'"external": "{manual_external}"')
        )
    if fmt == "jsonc":
        return (
            "// "
            + manual_comment
            + "\n"
            + _replace_first(text, '"external": "keep"', f'"external": "{manual_external}"')
        )
    if fmt == "line":
        profile = _line_profile(variant)
        return (
            _replace_first(
                text,
                f"{profile['tail_key']}=keep",
                f"{profile['tail_key']}={manual_external}",
            )
            + f"# {manual_comment}\n"
        )
    raise ValueError(fmt)


def _apply_manual_edit_to_file(
    fmt: str, variant: int, path: Path, *, manual_comment: str, manual_external: str
) -> None:
    path.write_text(
        _apply_manual_edit(
            fmt,
            variant,
            path.read_text(encoding="utf-8"),
            manual_comment=manual_comment,
            manual_external=manual_external,
        ),
        encoding="utf-8",
    )


def _apply_manual_semantic_edit(
    fmt: str, variant: int, semantic: dict[str, object], *, manual_external: str
) -> dict[str, object]:
    if fmt == "line":
        profile = _line_profile(variant)
        updated = dict(semantic)
        updated[profile["tail_key"]] = manual_external
        return updated
    updated = {key: value for key, value in semantic.items()}
    _set_nested_value(updated, _mapping_manual_path(variant), manual_external)
    return updated


def _mapping_manual_path(variant: int) -> list[str]:
    if variant == 0:
        return ["external"]
    if variant == 1:
        return ["meta", "external"]
    if variant == 2:
        return ["meta", "external"]
    raise ValueError(variant)


def _set_nested_value(root: dict[str, object], path: list[str], value: object) -> None:
    node: dict[str, object] = root
    for part in path[:-1]:
        next_node = node[part]
        if not isinstance(next_node, dict):
            raise TypeError(f"expected nested mapping at {part!r}")
        node = next_node
    node[path[-1]] = value


def _line_profile(variant: int) -> dict[str, object]:
    if variant == 0:
        return {
            "managed_block_id": "managed",
            "managed_lines": ["alpha=1", "beta=2"],
            "tail_key": "footer",
            "head_key": "header",
            "managed_lines_for_round": lambda round_index: [
                f"alpha={round_index + 2}",
                f"beta={round_index + 3}",
            ],
        }
    if variant == 1:
        return {
            "managed_block_id": "managed-alt",
            "managed_lines": ["alpha=1", "beta=2", "gamma=3"],
            "tail_key": "postscript",
            "head_key": "prefix",
            "managed_lines_for_round": lambda round_index: [
                f"alpha={round_index + 2}",
                f"beta={round_index + 3}",
                f"gamma={round_index + 4}",
            ],
        }
    raise ValueError(variant)


def _replace_first(text: str, old: str, new: str) -> str:
    if old not in text:
        raise AssertionError(f"expected to find {old!r} in text")
    return text.replace(old, new, 1)


def _failure_report(
    chain: list[str],
    target_path: Path,
    *,
    expected_text: str | None = None,
    actual_text: str | None = None,
    expected_semantic: object | None = None,
    actual_semantic: object | None = None,
) -> str:
    sections = ["rollback fuzz failure", *chain, f"target={target_path}"]
    if expected_semantic is not None or actual_semantic is not None:
        sections.append(f"expected_semantic={expected_semantic!r}")
        sections.append(f"actual_semantic={actual_semantic!r}")
    if expected_text is not None or actual_text is not None:
        sections.append("expected_text:")
        sections.append(expected_text or "<none>")
        sections.append("actual_text:")
        sections.append(actual_text or "<none>")
    return "\n".join(sections)
