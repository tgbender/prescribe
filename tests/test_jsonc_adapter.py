from __future__ import annotations

from pathlib import Path

import pytest

from prescribe.adapters.jsonc import JsoncAdapter
from prescribe.jsonc import JsoncParseError, modify_text, parse_jsonc, parse_tree
from prescribe.jsonc import _MISSING


JSONC_SAMPLE = (
    "{\n"
    "  // header\n"
    '  "title": "hello",\n'
    '  "count": 1,\n'
    "  // keep this comment\n"
    '  "nested": {\n'
    '    "value": true\n'
    "  }\n"
    "}\n"
)


def test_jsonc_round_trip_preserves_comments_and_structure(tmp_path: Path) -> None:
    source = tmp_path / "settings.json"
    source.write_text(JSONC_SAMPLE)

    adapter = JsoncAdapter()
    document = adapter.load(source)

    document.root["count"] = 2
    document.root["nested"]["extra"] = "new"

    target = tmp_path / "output.json"
    adapter.dump(document, target)

    text = target.read_text()
    assert text.startswith("{\n")
    assert "// header" in text
    assert "// keep this comment" in text
    assert '"count": 2' in text
    assert '"extra": "new"' in text
    assert text.count("\n") > 1


@pytest.mark.parametrize(
    "text",
    [
        '{"key": trueish}',
        '{"key": falsey}',
        '{"key": nullable}',
    ],
)
def test_jsonc_parser_rejects_keywords_without_word_boundary(text: str) -> None:
    with pytest.raises(JsoncParseError):
        parse_jsonc(text)


@pytest.mark.parametrize(
    "text, expected_substring",
    [
        ('{"key": trueish}', "trueish"),
        ('{"key": falsey}', "falsey"),
        ('{"key": nullable}', "nullable"),
    ],
)
def test_jsonc_tree_parser_reports_clear_error_for_keyword_boundary_violation(
    text: str, expected_substring: str
) -> None:

    with pytest.raises(JsoncParseError, match=expected_substring):
        parse_tree(text)


def test_jsonc_delete_first_array_item() -> None:
    text = '["alpha", "beta", "gamma"]'
    result = modify_text(text, [0], _MISSING)
    parsed = parse_jsonc(result)
    assert parsed == ["beta", "gamma"]


def test_jsonc_delete_middle_array_item() -> None:
    text = '["alpha", "beta", "gamma"]'
    result = modify_text(text, [1], _MISSING)
    parsed = parse_jsonc(result)
    assert parsed == ["alpha", "gamma"]


def test_jsonc_delete_last_array_item() -> None:
    text = '["alpha", "beta", "gamma"]'
    result = modify_text(text, [2], _MISSING)
    parsed = parse_jsonc(result)
    assert parsed == ["alpha", "beta"]


def test_jsonc_delete_only_array_item() -> None:
    text = '["only"]'
    result = modify_text(text, [0], _MISSING)
    parsed = parse_jsonc(result)
    assert parsed == []


def test_jsonc_delete_first_array_item_with_whitespace() -> None:
    text = '[ "alpha" , "beta" , "gamma" ]'
    result = modify_text(text, [0], _MISSING)
    parsed = parse_jsonc(result)
    assert parsed == ["beta", "gamma"]


def test_jsonc_delete_array_item_multiline() -> None:
    text = '[\n  "alpha",\n  "beta",\n  "gamma"\n]'
    result = modify_text(text, [0], _MISSING)
    parsed = parse_jsonc(result)
    assert parsed == ["beta", "gamma"]


def test_jsonc_crlf_preserved_when_adding_nested_object() -> None:
    text = '{\r\n  "title": "hello"\r\n}\r\n'
    result = modify_text(text, ["nested"], {"a": 1, "b": 2})
    lines = result.splitlines(True)
    for line in lines:
        if line.strip():
            assert line.endswith("\r\n"), f"expected CRLF: {repr(line)}"


def test_jsonc_crlf_preserved_when_appending_simple_value() -> None:
    text = '{\r\n  "title": "hello"\r\n}\r\n'
    result = modify_text(text, ["count"], 42)
    lines = result.splitlines(True)
    for line in lines:
        if line.strip():
            assert line.endswith("\r\n"), f"expected CRLF: {repr(line)}"


def test_jsonc_crlf_preserved_when_appending_array_item() -> None:
    text = '{\r\n  "items": [1, 2]\r\n}\r\n'
    result = modify_text(text, ["items", 2], 3)
    lines = result.splitlines(True)
    for line in lines:
        if line.strip():
            assert line.endswith("\r\n"), f"expected CRLF: {repr(line)}"


def test_jsonc_indent_multiline_does_not_prefix_bare_newlines() -> None:
    from prescribe.jsonc import _indent_multiline

    result = _indent_multiline("{\n\n}", "  ")
    assert result.splitlines(True)[1] == "\n"


def test_jsonc_render_value_respects_custom_indent() -> None:
    from prescribe.jsonc import _render_value

    result = _render_value({"a": 1}, indent="    ")
    assert '    "a": 1' in result
