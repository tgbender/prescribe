"""Read-only extraction helpers for shell environment snippets."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ShellEnvOperation = Literal["set", "path_set", "path_prepend", "path_append"]


@dataclass(frozen=True)
class ShellExtractionIssue:
    """A shell line that looked relevant but was intentionally not parsed."""

    shell_type: str
    line_number: int
    raw: str
    reason: str


@dataclass(frozen=True)
class ShellEnvUpdate:
    """A static environment update read from a shell file."""

    name: str
    operation: ShellEnvOperation
    shell_type: str
    line_number: int
    raw: str
    value: str | None = None
    entries: tuple[str, ...] = ()
    exported: bool = False
    conditional: bool = False


@dataclass(frozen=True)
class ShellExtraction:
    """Structured, read-only observations extracted from a shell file."""

    updates: tuple[ShellEnvUpdate, ...]
    issues: tuple[ShellExtractionIssue, ...] = ()


_POSIX_ASSIGNMENT_RE = re.compile(r"^(?P<export>export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")
_PWsh_ASSIGNMENT_RE = re.compile(r"^\$env:(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$", re.I)
_CMD_ASSIGNMENT_RE = re.compile(r"^set\s+\"?(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*?)\"?$", re.I)
_FISH_ASSIGNMENT_RE = re.compile(
    r"^set\s+(?P<flags>(?:-[A-Za-z]+\s+)*)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<value>.*)$"
)
_XONSH_ASSIGNMENT_RE = re.compile(r"^\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$")

_POSIX_SHELLS = {"bash", "zsh"}
_PATH_LIST_NAMES = frozenset(
    {
        "CDPATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
        "FPATH",
        "INFOPATH",
        "LD_LIBRARY_PATH",
        "MANPATH",
        "PATH",
        "PATHEXT",
        "PERL5LIB",
        "PKG_CONFIG_PATH",
        "PSMODULEPATH",
        "PYTHONPATH",
    }
)
_DYNAMIC_MARKERS = ("$(", "`", "<(")
_LineParser = Callable[[str, str, int], ShellEnvUpdate | ShellExtractionIssue | None]


@dataclass(frozen=True)
class _LineContext:
    parseable_line: str
    in_function: bool
    conditional: bool


class _ExtractionContext:
    def __init__(self, *, shell_type: str) -> None:
        self.shell_type = shell_type
        self._function_depth = 0
        self._conditional_depth = 0

    def classify(self, raw_line: str) -> _LineContext:
        if self.shell_type in _POSIX_SHELLS:
            return self._classify_posix(raw_line)
        if self.shell_type == "pwsh":
            return self._classify_pwsh(raw_line)
        return _LineContext(parseable_line=raw_line, in_function=False, conditional=False)

    def _classify_posix(self, raw_line: str) -> _LineContext:
        stripped = raw_line.strip()
        was_in_function = self._function_depth > 0
        conditional = self._conditional_depth > 0

        if was_in_function:
            self._function_depth = max(0, self._function_depth + _count_shell_blocks(stripped))
            return _LineContext(parseable_line=raw_line, in_function=True, conditional=conditional)

        if _starts_posix_function(stripped):
            self._function_depth = max(0, self._function_depth + _count_shell_blocks(stripped))
            return _LineContext(parseable_line=raw_line, in_function=True, conditional=conditional)

        parseable_line = _strip_posix_case_prefix(raw_line)
        if stripped.startswith(("if ", "if\t", "case ", "case\t", "for ", "for\t", "while ", "while\t")):
            self._conditional_depth += 1
        if stripped in {"fi", "esac", "done"} or stripped.startswith(("fi ", "esac ", "done ")):
            self._conditional_depth = max(0, self._conditional_depth - 1)
        return _LineContext(parseable_line=parseable_line, in_function=False, conditional=conditional)

    def _classify_pwsh(self, raw_line: str) -> _LineContext:
        stripped = raw_line.strip()
        was_in_function = self._function_depth > 0
        if stripped.lower().startswith("function "):
            self._function_depth = max(0, self._function_depth + _count_braces(stripped))
            return _LineContext(parseable_line=raw_line, in_function=True, conditional=False)
        if was_in_function:
            self._function_depth = max(0, self._function_depth + _count_braces(stripped))
            return _LineContext(parseable_line=raw_line, in_function=True, conditional=False)
        return _LineContext(parseable_line=raw_line, in_function=False, conditional=False)


def extract_shell_env(text: str, *, shell_type: str = "bash") -> ShellExtraction:
    """Extract static env assignments from shell source text without evaluating it.

    This helper is intentionally conservative. It accepts simple literal
    assignments and PATH-relative mutations, and reports dynamic lines as issues
    instead of trying to emulate a shell.
    """

    parser = _parser_for_shell(shell_type)
    context = _ExtractionContext(shell_type=shell_type)
    updates: list[ShellEnvUpdate] = []
    issues: list[ShellExtractionIssue] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line_context = context.classify(raw_line)
        if line_context.in_function:
            continue
        parsed = parser(line_context.parseable_line, shell_type, line_number)
        if parsed is None:
            continue
        if isinstance(parsed, ShellExtractionIssue):
            issues.append(parsed)
        else:
            updates.append(_with_context(parsed, conditional=line_context.conditional, raw=raw_line))
    return ShellExtraction(updates=tuple(updates), issues=tuple(issues))


def extract_shell_env_file(
    path: str | Path, *, shell_type: str | None = None, encoding: str = "utf-8"
) -> ShellExtraction:
    """Extract static env assignments from a shell file."""

    shell_path = Path(path)
    inferred_shell = shell_type or infer_shell_type(shell_path)
    return extract_shell_env(shell_path.read_text(encoding=encoding), shell_type=inferred_shell)


def infer_shell_type(path: str | Path) -> str:
    """Infer a supported shell type from a common shell config filename."""

    shell_path = Path(path)
    name = shell_path.name.lower()
    suffix = shell_path.suffix.lower()
    if name in {".bashrc", ".bash_profile", ".profile"} or suffix == ".bash":
        return "bash"
    if name == ".zshrc" or suffix == ".zsh":
        return "zsh"
    if name in {"config.fish"} or suffix == ".fish":
        return "fish"
    if name in {".xonshrc", "xonshrc"} or suffix == ".xsh":
        return "xonsh"
    if suffix == ".ps1":
        return "pwsh"
    if suffix in {".cmd", ".bat"}:
        return "cmd"
    return "bash"


def _parser_for_shell(shell_type: str) -> _LineParser:
    if shell_type in _POSIX_SHELLS:
        return _parse_posix_line
    if shell_type == "xonsh":
        return _parse_xonsh_line
    if shell_type == "fish":
        return _parse_fish_line
    if shell_type == "pwsh":
        return _parse_pwsh_line
    if shell_type == "cmd":
        return _parse_cmd_line
    raise ValueError(f"unsupported shell type: {shell_type!r}")


def _parse_posix_line(raw_line: str, shell_type: str, line_number: int) -> ShellEnvUpdate | ShellExtractionIssue | None:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    match = _POSIX_ASSIGNMENT_RE.match(line)
    if match is None:
        return None
    name = match.group("name")
    raw_value = _strip_inline_comment(match.group("value").strip())
    if raw_value.startswith("("):
        return _issue(shell_type, line_number, raw_line, "array assignment is unsupported")
    value = _parse_scalar(raw_value)
    if value is None or _is_dynamic(value):
        return _issue(shell_type, line_number, raw_line, "dynamic or unsupported assignment")
    return _env_update(
        name=name,
        value=value,
        shell_type=shell_type,
        line_number=line_number,
        raw=raw_line,
        exported=match.group("export") is not None,
        path_separator=":",
        path_self_refs=(f"${name}", f"${{{name}}}"),
    )


def _parse_pwsh_line(raw_line: str, shell_type: str, line_number: int) -> ShellEnvUpdate | ShellExtractionIssue | None:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    match = _PWsh_ASSIGNMENT_RE.match(line)
    if match is None:
        return None
    name = match.group("name")
    raw_value = _strip_inline_comment(match.group("value").strip())
    value = _parse_scalar(raw_value)
    if value is None or _is_dynamic(value):
        return _issue(shell_type, line_number, raw_line, "dynamic or unsupported assignment")
    return _env_update(
        name=name,
        value=value,
        shell_type=shell_type,
        line_number=line_number,
        raw=raw_line,
        path_separator=";",
        path_self_refs=(f"$env:{name}", f"${{env:{name}}}"),
    )


def _parse_cmd_line(raw_line: str, shell_type: str, line_number: int) -> ShellEnvUpdate | ShellExtractionIssue | None:
    line = raw_line.strip()
    if not line or line.lower().startswith("rem ") or line.startswith("::"):
        return None
    match = _CMD_ASSIGNMENT_RE.match(line)
    if match is None:
        return None
    name = match.group("name")
    value = match.group("value")
    if _is_dynamic(value):
        return _issue(shell_type, line_number, raw_line, "dynamic or unsupported assignment")
    return _env_update(
        name=name,
        value=value,
        shell_type=shell_type,
        line_number=line_number,
        raw=raw_line,
        path_separator=";",
        path_self_refs=(f"%{name}%",),
    )


def _parse_fish_line(raw_line: str, shell_type: str, line_number: int) -> ShellEnvUpdate | ShellExtractionIssue | None:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    match = _FISH_ASSIGNMENT_RE.match(line)
    if match is None:
        return None
    name = match.group("name")
    raw_value = match.group("value").strip()
    values = _parse_fish_values(raw_value)
    if values is None:
        return _issue(shell_type, line_number, raw_line, "dynamic or unsupported assignment")
    if _is_path_name(name):
        return ShellEnvUpdate(
            name=name,
            operation="path_set",
            shell_type=shell_type,
            line_number=line_number,
            raw=raw_line,
            entries=tuple(values),
            exported="-x" in (match.group("flags") or "") or "-gx" in (match.group("flags") or ""),
        )
    return ShellEnvUpdate(
        name=name,
        operation="set",
        value=" ".join(values),
        shell_type=shell_type,
        line_number=line_number,
        raw=raw_line,
        exported="-x" in (match.group("flags") or "") or "-gx" in (match.group("flags") or ""),
    )


def _parse_xonsh_line(raw_line: str, shell_type: str, line_number: int) -> ShellEnvUpdate | ShellExtractionIssue | None:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    match = _XONSH_ASSIGNMENT_RE.match(line)
    if match is None:
        return None
    name = match.group("name")
    raw_value = _strip_inline_comment(match.group("value").strip())
    try:
        literal = ast.literal_eval(raw_value)
    except (SyntaxError, ValueError):
        value = _parse_scalar(raw_value)
        if value is None or _is_dynamic(value):
            return _issue(shell_type, line_number, raw_line, "dynamic or unsupported assignment")
        return _env_update(
            name=name,
            value=value,
            shell_type=shell_type,
            line_number=line_number,
            raw=raw_line,
            path_separator=":",
            path_self_refs=(f"${name}",),
        )
    if isinstance(literal, str):
        return _env_update(
            name=name,
            value=literal,
            shell_type=shell_type,
            line_number=line_number,
            raw=raw_line,
            path_separator=":",
            path_self_refs=(f"${name}",),
        )
    if isinstance(literal, list) and all(isinstance(item, str) for item in literal):
        return ShellEnvUpdate(
            name=name,
            operation="path_set" if _is_path_name(name) else "set",
            value=None if _is_path_name(name) else ":".join(literal),
            entries=tuple(literal) if _is_path_name(name) else (),
            shell_type=shell_type,
            line_number=line_number,
            raw=raw_line,
        )
    return _issue(shell_type, line_number, raw_line, "dynamic or unsupported assignment")


def _env_update(
    *,
    name: str,
    value: str,
    shell_type: str,
    line_number: int,
    raw: str,
    path_separator: str,
    path_self_refs: tuple[str, ...],
    exported: bool = False,
) -> ShellEnvUpdate:
    if not _is_path_name(name):
        return ShellEnvUpdate(
            name=name,
            operation="set",
            value=value,
            shell_type=shell_type,
            line_number=line_number,
            raw=raw,
            exported=exported,
        )
    parts = tuple(part for part in value.split(path_separator) if part)
    self_indexes = [index for index, part in enumerate(parts) if part in path_self_refs]
    if not self_indexes:
        return ShellEnvUpdate(
            name=name,
            operation="path_set",
            entries=parts,
            shell_type=shell_type,
            line_number=line_number,
            raw=raw,
            exported=exported,
        )
    if len(self_indexes) == 1 and self_indexes[0] == len(parts) - 1 and parts[:-1]:
        return ShellEnvUpdate(
            name=name,
            operation="path_prepend",
            entries=parts[:-1],
            shell_type=shell_type,
            line_number=line_number,
            raw=raw,
            exported=exported,
        )
    if len(self_indexes) == 1 and self_indexes[0] == 0 and parts[1:]:
        return ShellEnvUpdate(
            name=name,
            operation="path_append",
            entries=parts[1:],
            shell_type=shell_type,
            line_number=line_number,
            raw=raw,
            exported=exported,
        )
    return ShellEnvUpdate(
        name=name,
        operation="set",
        value=value,
        shell_type=shell_type,
        line_number=line_number,
        raw=raw,
        exported=exported,
    )


def _parse_scalar(raw_value: str) -> str | None:
    if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {"'", '"'}:
        quote = raw_value[0]
        inner = raw_value[1:-1]
        if quote == "'":
            return inner
        return inner.replace(r"\\", "\\").replace(r"\"", '"').replace(r"\$", "$").replace(r"\`", "`")
    if any(char.isspace() for char in raw_value):
        return None
    return raw_value


def _parse_fish_values(raw_value: str) -> list[str] | None:
    if not raw_value:
        return [""]
    values: list[str] = []
    index = 0
    while index < len(raw_value):
        while index < len(raw_value) and raw_value[index].isspace():
            index += 1
        if index >= len(raw_value):
            break
        if raw_value[index] in {"'", '"'}:
            quote = raw_value[index]
            end = raw_value.find(quote, index + 1)
            if end == -1:
                return None
            values.append(raw_value[index + 1 : end])
            index = end + 1
            continue
        end = index
        while end < len(raw_value) and not raw_value[end].isspace():
            end += 1
        values.append(raw_value[index:end])
        index = end
    if any(_is_dynamic(value) for value in values):
        return None
    return values


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "#" and quote is None and index > 0 and value[index - 1].isspace():
            return value[:index].rstrip()
    return value


def _is_dynamic(value: str) -> bool:
    return any(marker in value for marker in _DYNAMIC_MARKERS)


def _is_path_name(name: str) -> bool:
    return name.upper() in _PATH_LIST_NAMES


def _with_context(update: ShellEnvUpdate, *, conditional: bool, raw: str) -> ShellEnvUpdate:
    if not conditional and update.raw == raw:
        return update
    return ShellEnvUpdate(
        name=update.name,
        operation=update.operation,
        shell_type=update.shell_type,
        line_number=update.line_number,
        raw=raw,
        value=update.value,
        entries=update.entries,
        exported=update.exported,
        conditional=conditional,
    )


def _starts_posix_function(stripped: str) -> bool:
    if not stripped or stripped.startswith("#"):
        return False
    return bool(re.match(r"^(function\s+)?[A-Za-z_][A-Za-z0-9_-]*(\s*\(\))?\s*\{", stripped))


def _count_shell_blocks(stripped: str) -> int:
    return stripped.count("{") - stripped.count("}")


def _count_braces(stripped: str) -> int:
    return stripped.count("{") - stripped.count("}")


def _strip_posix_case_prefix(raw_line: str) -> str:
    stripped = raw_line.strip()
    match = re.match(r"^(?:\*|[A-Za-z0-9_./:@%+-]+)\)\s*(?P<body>.+?)\s*(?:;;)?$", stripped)
    if match is None:
        return raw_line
    return match.group("body")


def _issue(shell_type: str, line_number: int, raw: str, reason: str) -> ShellExtractionIssue:
    return ShellExtractionIssue(shell_type=shell_type, line_number=line_number, raw=raw, reason=reason)
