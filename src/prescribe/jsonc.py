import json
from dataclasses import dataclass, field
from typing import Any

from prescribe._util import _MISSING


@dataclass(slots=True)
class JsoncNode:
    type: str
    offset: int
    length: int
    value: Any = None
    colon_offset: int | None = None
    parent: "JsoncNode | None" = None
    children: list["JsoncNode"] = field(default_factory=list)


class JsoncParseError(ValueError):
    pass


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.length = len(text)
        self.index = 0

    def parse(self) -> "JsoncNode | None":
        self.index = self._skip_trivia(0)
        if self.index >= self.length:
            return None
        node = self._parse_value(None)
        self.index = self._skip_trivia(self.index)
        return node

    def _parse_value(self, parent: "JsoncNode | None") -> "JsoncNode":
        self.index = self._skip_trivia(self.index)
        if self.index >= self.length:
            raise JsoncParseError("unexpected end of input")

        ch = self.text[self.index]
        if ch == "{":
            return self._parse_object(parent)
        if ch == "[":
            return self._parse_array(parent)
        if ch == '"':
            return self._parse_string(parent)
        if ch == "-" or ch.isdigit():
            return self._parse_number(parent)
        if self.text.startswith("true", self.index) and not self._is_ident_char(self.index + 4):
            return self._parse_literal(parent, "true", True)
        if self.text.startswith("false", self.index) and not self._is_ident_char(self.index + 5):
            return self._parse_literal(parent, "false", False)
        if self.text.startswith("null", self.index) and not self._is_ident_char(self.index + 4):
            return self._parse_literal(parent, "null", None)
        if ch.isalpha() or ch == "_" or ch == "$":
            end = self.index
            while end < self.length and (self.text[end].isalnum() or self.text[end] in "_$"):
                end += 1
            ident = self.text[self.index : end]
            raise JsoncParseError(f"unexpected identifier {ident!r} at {self.index}")
        raise JsoncParseError(f"unexpected character {ch!r} at {self.index}")

    def _parse_object(self, parent: "JsoncNode | None") -> "JsoncNode":
        start = self.index
        self.index += 1
        node = JsoncNode(type="object", offset=start, length=-1, parent=parent)
        self.index = self._skip_trivia(self.index)
        if self.index < self.length and self.text[self.index] == "}":
            self.index += 1
            node.length = self.index - start
            return node

        while True:
            self.index = self._skip_trivia(self.index)
            key = self._parse_string(None)
            self.index = self._skip_trivia(self.index)
            if self.index >= self.length or self.text[self.index] != ":":
                raise JsoncParseError(f"expected ':' after object key at {self.index}")
            colon_offset = self.index
            self.index += 1
            value = self._parse_value(None)
            prop = JsoncNode(
                type="property",
                offset=key.offset,
                length=value.offset + value.length - key.offset,
                colon_offset=colon_offset,
                parent=node,
            )
            key.parent = prop
            value.parent = prop
            prop.children = [key, value]
            node.children.append(prop)
            self.index = self._skip_trivia(self.index)
            if self.index < self.length and self.text[self.index] == ",":
                self.index += 1
                self.index = self._skip_trivia(self.index)
                if self.index < self.length and self.text[self.index] == "}":
                    self.index += 1
                    node.length = self.index - start
                    return node
                continue
            if self.index < self.length and self.text[self.index] == "}":
                self.index += 1
                node.length = self.index - start
                return node
            raise JsoncParseError(f"expected ',' or '}}' at {self.index}")

    def _parse_array(self, parent: "JsoncNode | None") -> "JsoncNode":
        start = self.index
        self.index += 1
        node = JsoncNode(type="array", offset=start, length=-1, parent=parent)
        self.index = self._skip_trivia(self.index)
        if self.index < self.length and self.text[self.index] == "]":
            self.index += 1
            node.length = self.index - start
            return node

        while True:
            value = self._parse_value(node)
            node.children.append(value)
            self.index = self._skip_trivia(self.index)
            if self.index < self.length and self.text[self.index] == ",":
                self.index += 1
                self.index = self._skip_trivia(self.index)
                if self.index < self.length and self.text[self.index] == "]":
                    self.index += 1
                    node.length = self.index - start
                    return node
                continue
            if self.index < self.length and self.text[self.index] == "]":
                self.index += 1
                node.length = self.index - start
                return node
            raise JsoncParseError(f"expected ',' or ']' at {self.index}")

    def _parse_string(self, parent: "JsoncNode | None") -> "JsoncNode":
        start = self.index
        if self.text[self.index] != '"':
            raise JsoncParseError(f"expected string at {self.index}")
        self.index += 1
        value_chars: list[str] = []
        while self.index < self.length:
            ch = self.text[self.index]
            if ch == '"':
                self.index += 1
                return JsoncNode(
                    type="string",
                    offset=start,
                    length=self.index - start,
                    value="".join(value_chars),
                    parent=parent,
                )
            if ch == "\\":
                if self.index + 1 >= self.length:
                    raise JsoncParseError("unterminated escape sequence")
                esc = self.text[self.index + 1]
                self.index += 2
                if esc == '"':
                    value_chars.append('"')
                elif esc == "\\":
                    value_chars.append("\\")
                elif esc == "/":
                    value_chars.append("/")
                elif esc == "b":
                    value_chars.append("\b")
                elif esc == "f":
                    value_chars.append("\f")
                elif esc == "n":
                    value_chars.append("\n")
                elif esc == "r":
                    value_chars.append("\r")
                elif esc == "t":
                    value_chars.append("\t")
                elif esc == "u":
                    if self.index + 4 > self.length:
                        raise JsoncParseError("invalid unicode escape")
                    digits = self.text[self.index : self.index + 4]
                    if any(c not in "0123456789abcdefABCDEF" for c in digits):
                        raise JsoncParseError("invalid unicode escape")
                    value_chars.append(chr(int(digits, 16)))
                    self.index += 4
                else:
                    raise JsoncParseError(f"invalid escape sequence \\{esc}")
                continue
            if ch in "\r\n":
                raise JsoncParseError("unterminated string")
            value_chars.append(ch)
            self.index += 1
        raise JsoncParseError("unterminated string")

    def _parse_number(self, parent: "JsoncNode | None") -> "JsoncNode":
        start = self.index
        if self.text[self.index] == "-":
            self.index += 1
        while self.index < self.length and self.text[self.index].isdigit():
            self.index += 1
        if self.index < self.length and self.text[self.index] == ".":
            self.index += 1
            while self.index < self.length and self.text[self.index].isdigit():
                self.index += 1
        if self.index < self.length and self.text[self.index] in "eE":
            self.index += 1
            if self.index < self.length and self.text[self.index] in "+-":
                self.index += 1
            while self.index < self.length and self.text[self.index].isdigit():
                self.index += 1
        raw = self.text[start : self.index]
        return JsoncNode(
            type="number",
            offset=start,
            length=self.index - start,
            value=json.loads(raw),
            parent=parent,
        )

    def _parse_literal(self, parent: "JsoncNode | None", literal: str, value: Any) -> "JsoncNode":
        start = self.index
        self.index += len(literal)
        return JsoncNode(
            type="literal",
            offset=start,
            length=len(literal),
            value=value,
            parent=parent,
        )

    def _is_ident_char(self, index: int) -> bool:
        if index >= self.length:
            return False
        ch = self.text[index]
        return ch.isalnum() or ch == "_" or ch == "$"

    def _skip_trivia(self, index: int) -> int:
        while index < self.length:
            ch = self.text[index]
            if ch in " \t\r\n":
                index += 1
                continue
            if self.text.startswith("//", index):
                index += 2
                while index < self.length and self.text[index] not in "\r\n":
                    index += 1
                continue
            if self.text.startswith("/*", index):
                end = self.text.find("*/", index + 2)
                if end == -1:
                    raise JsoncParseError("unterminated block comment")
                index = end + 2
                continue
            break
        return index


def parse_tree(text: str) -> "JsoncNode | None":
    return _Parser(text).parse()


def find_node_at_location(root: "JsoncNode | None", path: list[str | int]) -> "JsoncNode | None":
    if root is None:
        return None
    node = root
    for segment in path:
        if isinstance(segment, str):
            if node.type != "object":
                return None
            found = None
            for prop in node.children:
                key = prop.children[0]
                if key.value == segment:
                    found = prop.children[1]
                    break
            if found is None:
                return None
            node = found
        else:
            if node.type != "array":
                return None
            if segment < 0 or segment >= len(node.children):
                return None
            node = node.children[segment]
    return node


def parse_jsonc(text: str) -> Any:
    cleaned = _strip_comments(text)
    cleaned = _strip_trailing_commas(cleaned)
    if not cleaned.strip():
        return None
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise JsoncParseError(str(exc)) from exc


def diff_paths(original: Any, current: Any, prefix: list[str | int] | None = None) -> list[tuple[list[str | int], Any]]:
    prefix = [] if prefix is None else prefix
    diffs: list[tuple[list[str | int], Any]] = []
    if type(original) is not type(current):
        diffs.append((prefix, current))
        return diffs
    if isinstance(original, dict):
        for key in original:
            if key not in current:
                diffs.append(([*prefix, key], _MISSING))
        for key in current:
            if key not in original:
                diffs.append(([*prefix, key], current[key]))
            else:
                diffs.extend(diff_paths(original[key], current[key], [*prefix, key]))
        return diffs
    if isinstance(original, list):
        if original != current:
            diffs.append((prefix, current))
        return diffs
    if original != current:
        diffs.append((prefix, current))
    return diffs


def modify_text(text: str, path: list[str | int], value: Any, *, indent: str = "  ") -> str:
    root = parse_tree(text)
    eol = _guess_eol(text)
    if not path:
        return _render_value(value, eol=eol) + eol

    parent_path = path[:-1]
    last = path[-1]
    parent = find_node_at_location(root, parent_path)
    if parent is None:
        raise JsoncParseError(f"path not found: {path}")

    if isinstance(last, str):
        if parent.type != "object":
            raise JsoncParseError(f"cannot set property on {parent.type}")
        return _modify_object(text, parent, last, value, indent=indent, eol=eol)
    if parent.type != "array":
        raise JsoncParseError(f"cannot set array index on {parent.type}")
    return _modify_array(text, parent, int(last), value, indent=indent, eol=eol)


def _modify_object(text: str, parent: JsoncNode, key: str, value: Any, *, indent: str, eol: str) -> str:
    existing = None
    for prop in parent.children:
        if prop.children[0].value == key:
            existing = prop
            break
    if existing is not None:
        if value is _MISSING:
            return _delete_property(text, parent, existing)
        replacement = _render_value(value, eol=eol)
        start = existing.children[1].offset
        end = existing.children[1].offset + existing.children[1].length
        return text[:start] + replacement + text[end:]

    insertion = _render_property(text, parent, key, value, indent=indent, eol=eol)
    close = parent.offset + parent.length - 1
    if not parent.children:
        line_indent = _line_indent(text, parent.offset)
        body = f"{eol}{line_indent}{indent}{insertion}{eol}{line_indent}"
        return text[: parent.offset + 1] + body + text[close:]

    prev = parent.children[-1]
    prev_end = prev.offset + prev.length
    eol_local = eol
    gap = text[prev_end:close]
    if "\n" in gap or "\r" in gap:
        sep = f",{eol_local}{_line_indent(text, prev.offset)}"
        return text[:prev_end] + sep + insertion + text[close:]
    return text[:prev_end] + f", {insertion}" + text[close:]


def _modify_array(text: str, parent: JsoncNode, index: int, value: Any, *, indent: str, eol: str) -> str:
    if value is _MISSING:
        if index < 0 or index >= len(parent.children):
            return text
        return _delete_array_item(text, parent, index)
    rendered = _render_value(value, eol=eol)
    close = parent.offset + parent.length - 1
    if index >= len(parent.children):
        if not parent.children:
            line_indent = _line_indent(text, parent.offset)
            body = f"{eol}{line_indent}{indent}{rendered}{eol}{line_indent}"
            return text[: parent.offset + 1] + body + text[close:]
        prev = parent.children[-1]
        prev_end = prev.offset + prev.length
        gap = text[prev_end:close]
        if "\n" in gap or "\r" in gap:
            item_indent = _line_indent(text, prev.offset)
            rendered = _indent_multiline(rendered, item_indent)
            return text[:prev_end] + f",{eol}{item_indent}{rendered}" + text[close:]
        return text[:prev_end] + f", {rendered}" + text[close:]
    item = parent.children[index]
    return text[: item.offset] + rendered + text[item.offset + item.length :]


def _delete_property(text: str, parent: JsoncNode, prop: JsoncNode) -> str:
    if len(parent.children) == 1:
        return text[: parent.offset + 1] + text[parent.offset + parent.length - 1 :]
    idx = parent.children.index(prop)
    if idx == 0:
        next_prop = parent.children[1]
        start = parent.offset + 1
        end = next_prop.offset
        return text[:start] + text[end:]
    prev = parent.children[idx - 1]
    start = prev.offset + prev.length
    end = prop.offset + prop.length
    return text[:start] + text[end:]


def _delete_array_item(text: str, parent: JsoncNode, index: int) -> str:
    if len(parent.children) == 1:
        return text[: parent.offset + 1] + text[parent.offset + parent.length - 1 :]
    item = parent.children[index]
    if index == 0:
        end = item.offset + item.length
        while end < len(text) and text[end] in " \t\r\n":
            end += 1
        if end < len(text) and text[end] == ",":
            end += 1
        return text[: parent.offset + 1] + text[end:]
    prev = parent.children[index - 1]
    start = prev.offset + prev.length
    while start > 0 and text[start] in " \t":
        start -= 1
    if start > 0 and text[start] == ",":
        pass
    else:
        start = prev.offset + prev.length
    end = item.offset + item.length
    while end < len(text) and text[end] in " \t\r\n":
        end += 1
    return text[:start] + text[end:]


def _render_property(text: str, parent: JsoncNode, key: str, value: Any, *, indent: str, eol: str) -> str:
    return f"{json.dumps(key)}: {_render_value(value, indent=indent, eol=eol)}"


def _render_value(value: Any, *, indent: str = "  ", eol: str = "\n") -> str:
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, ensure_ascii=False, indent=indent, separators=(",", ": "))
        rendered = rendered.replace("\n", eol)
        return rendered
    return json.dumps(value, ensure_ascii=False)


def _indent_multiline(text: str, prefix: str) -> str:
    if "\n" not in text and "\r" not in text:
        return text
    lines = text.splitlines(True)
    if len(lines) <= 1:
        return text
    return lines[0] + "".join(prefix + line if line not in {"\n", "\r", "\r\n"} else line for line in lines[1:])


def _guess_eol(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\n" in text:
        return "\n"
    if "\r" in text:
        return "\r"
    return "\n"


def _line_indent(text: str, offset: int) -> str:
    line_start = max(text.rfind("\n", 0, offset), text.rfind("\r", 0, offset))
    if line_start == -1:
        return ""
    i = line_start + 1
    while i < len(text) and text[i] in " \t":
        i += 1
    return text[line_start + 1 : i]


def _strip_comments(text: str) -> str:
    result: list[str] = []
    i = 0
    in_string = False
    escape = False
    while i < len(text):
        ch = text[i]
        if in_string:
            result.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            result.append(ch)
            i += 1
            continue
        if text.startswith("//", i):
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end == -1:
                raise JsoncParseError("unterminated block comment")
            i = end + 2
            continue
        result.append(ch)
        i += 1
    return "".join(result)


def _strip_trailing_commas(text: str) -> str:
    result: list[str] = []
    i = 0
    in_string = False
    escape = False
    while i < len(text):
        ch = text[i]
        if in_string:
            result.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            result.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            if j < len(text) and text[j] in "}]":
                i += 1
                continue
        result.append(ch)
        i += 1
    return "".join(result)
