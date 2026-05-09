import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from prescribe.atomic import atomic_write_text
from prescribe.document import Document
from prescribe.encoding import read_utf8_text

_CRLF_EXTENSIONS = frozenset({".bat", ".cmd"})

_BEGIN_RE = re.compile(r"^# prescribe:begin (?P<block_id>.*\S)\s*$")
_END_RE = re.compile(r"^# prescribe:end (?P<block_id>.*\S)\s*$")


@dataclass(slots=True)
class LiteralSegment:
    lines: list[str] = field(default_factory=list)

    def render(self) -> str:
        return "".join(self.lines)


@dataclass(slots=True)
class ManagedBlock:
    block_id: str
    lines: list[str] = field(default_factory=list)
    header: str = ""
    footer: str = ""

    def render(self) -> str:
        return "".join([self.header, *self.lines, self.footer])


Segment = LiteralSegment | ManagedBlock


@dataclass(slots=True)
class LineDocument:
    path: Path
    format: str = "line"
    newline: str = "\n"
    segments: list[Segment] = field(default_factory=list)

    def block(self, block_id: str) -> ManagedBlock | None:
        for segment in self.segments:
            if isinstance(segment, ManagedBlock) and segment.block_id == block_id:
                return segment
        return None

    def ensure_block(self, block_id: str, entries: Sequence[str]) -> ManagedBlock:
        block = self.block(block_id)
        normalized = [_ensure_line_ending(entry, self.newline) for entry in entries]
        if block is None:
            block = ManagedBlock(
                block_id=block_id,
                lines=normalized,
                header=f"# prescribe:begin {block_id}{self.newline}",
                footer=f"# prescribe:end {block_id}{self.newline}",
            )
            self.segments.append(block)
            return block

        block.lines = normalized
        return block

    def remove_block(self, block_id: str) -> bool:
        for index, segment in enumerate(self.segments):
            if isinstance(segment, ManagedBlock) and segment.block_id == block_id:
                del self.segments[index]
                return True
        return False

    def render(self) -> str:
        output: list[str] = []
        for segment in self.segments:
            chunk = segment.render()
            if output and not output[-1].endswith(("\n", "\r")) and not chunk.startswith(("\n", "\r")):
                output.append(self.newline)
            output.append(chunk)
        return "".join(output)


def parse_line_document(path: Path, text: str) -> LineDocument:
    lines = text.splitlines(keepends=True)
    newline = preferred_newline(path, lines)
    segments: list[Segment] = []
    literal: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        begin_match = _BEGIN_RE.match(_rstrip_line_endings(line))
        if begin_match is None:
            literal.append(line)
            index += 1
            continue

        if literal:
            segments.append(LiteralSegment(lines=literal))
            literal = []

        block_id = begin_match.group("block_id")
        header = line
        index += 1
        body: list[str] = []
        footer: str | None = None

        while index < len(lines):
            line = lines[index]
            end_match = _END_RE.match(_rstrip_line_endings(line))
            if end_match is not None:
                end_id = end_match.group("block_id")
                if end_id != block_id:
                    raise ValueError(f"managed block mismatch in {path}: expected {block_id!r}, found {end_id!r}")
                footer = line
                index += 1
                break
            body.append(line)
            index += 1

        if footer is None:
            raise ValueError(f"unterminated managed block {block_id!r} in {path}")

        segments.append(ManagedBlock(block_id=block_id, lines=body, header=header, footer=footer))

    if literal:
        segments.append(LiteralSegment(lines=literal))

    return LineDocument(path=path, format="line", newline=newline, segments=segments)


class LineAdapter:
    format_name = "line"

    def load(self, path: Path) -> Document:
        raw = read_utf8_text(path)
        root = parse_line_document(path, raw)
        return Document(path=path, format=self.format_name, root=root)

    def dump(self, document: Document, path: Path) -> None:
        atomic_write_text(path, document.root.render(), newline="")


def _detect_newline(lines: Iterable[str]) -> str:
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\n"


def preferred_newline(path: Path, lines: Iterable[str] | None = None) -> str:
    """Determine the newline convention for a file.

    - .bat/.cmd files always use \\r\\n
    - Existing files: detect from content
    - New files: default to \\n
    """
    if path.suffix.lower() in _CRLF_EXTENSIONS:
        return "\r\n"
    if lines is not None:
        return _detect_newline(lines)
    return "\n"


def _rstrip_line_endings(line: str) -> str:
    return line.rstrip("\r\n")


def _ensure_line_ending(line: str, newline: str) -> str:
    if line.endswith(("\n", "\r")):
        return line
    return f"{line}{newline}"


line_adapter = LineAdapter()
