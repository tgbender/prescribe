from __future__ import annotations

from pathlib import Path

import tomlkit

from config_helper.atomic import atomic_write_text
from config_helper.document import Document


class TomlAdapter:
    format_name = "toml"

    def load(self, path: Path) -> Document:
        root = tomlkit.parse(path.read_text(encoding="utf-8"))
        return Document(path=path, format=self.format_name, root=root)

    def dump(self, document: Document, path: Path) -> None:
        atomic_write_text(path, tomlkit.dumps(document.root))


toml_adapter = TomlAdapter()
