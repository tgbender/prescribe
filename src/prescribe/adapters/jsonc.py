from copy import deepcopy
from pathlib import Path

from prescribe.atomic import atomic_write_text, normalize_newlines, preferred_text_newline
from prescribe.document import Document
from prescribe.encoding import read_utf8_text
from prescribe.jsonc import (
    diff_paths,
    modify_text,
    parse_jsonc,
    parse_tree,
)


class JsoncAdapter:
    format_name = "jsonc"

    def load(self, path: Path) -> Document:
        text = read_utf8_text(path)
        root = parse_jsonc(text) or {}
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
        if document.syntax is None or document.source_text is None or document.baseline_root is None:
            # Either a new file (no source text / baseline) or a file with no parseable
            # JSON structure (e.g., comment-only source). Fall back to standard JSON.
            import json

            newline = preferred_text_newline(path)
            atomic_write_text(
                path,
                normalize_newlines(json.dumps(document.root, indent=2, ensure_ascii=False) + "\n", newline),
                newline="",
            )
            return

        text = document.source_text
        diffs = diff_paths(document.baseline_root, document.root)
        for path_segments, value in diffs:
            text = modify_text(text, path_segments, value)
        atomic_write_text(path, text, newline="")


jsonc_adapter = JsoncAdapter()
