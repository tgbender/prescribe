from pathlib import Path

import tomlkit

from prescribe.atomic import atomic_write_text, normalize_newlines, preferred_text_newline
from prescribe.document import Document
from prescribe.encoding import read_utf8_text


class TomlAdapter:
    format_name = "toml"

    def load(self, path: Path) -> Document:
        root = tomlkit.parse(read_utf8_text(path))
        return Document(path=path, format=self.format_name, root=root)

    def dump(self, document: Document, path: Path) -> None:
        newline = preferred_text_newline(path)
        atomic_write_text(path, normalize_newlines(tomlkit.dumps(document.root), newline), newline="")


toml_adapter = TomlAdapter()
