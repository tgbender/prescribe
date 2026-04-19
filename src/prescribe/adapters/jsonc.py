from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from prescribe.atomic import atomic_write_text
from prescribe.document import Document
from prescribe.jsonc import (
    JsoncParseError,
    diff_paths,
    modify_text,
    parse_jsonc,
    parse_tree,
)


class JsoncAdapter:
    format_name = "jsonc"

    def load(self, path: Path) -> Document:
        text = path.read_text(encoding="utf-8")
        root = parse_jsonc(text)
        tree = parse_tree(text)
        return Document(
            path=path,
            format=self.format_name,
            root=root,
            source_text=text,
            baseline_root=deepcopy(root),
            syntax=tree,
        )

    def dump(self, document: Document, path: Path) -> None:
        if document.source_text is None or document.baseline_root is None:
            raise JsoncParseError("jsonc document is missing source text for round-trip editing")

        text = document.source_text
        diffs = diff_paths(document.baseline_root, document.root)
        for path_segments, value in diffs:
            text = modify_text(text, path_segments, value)
        atomic_write_text(path, text)


jsonc_adapter = JsoncAdapter()
