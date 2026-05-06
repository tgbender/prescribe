from pathlib import Path

from ruamel.yaml import YAML

from prescribe.atomic import atomic_write_text, normalize_newlines, preferred_text_newline
from prescribe.document import Document


class YamlAdapter:
    format_name = "yaml"

    def __init__(self) -> None:
        self._yaml = YAML(typ="rt")
        self._yaml.preserve_quotes = True

    def load(self, path: Path) -> Document:
        with path.open("r", encoding="utf-8") as handle:
            root = self._yaml.load(handle)
        return Document(path=path, format=self.format_name, root=root)

    def dump(self, document: Document, path: Path) -> None:
        from io import StringIO

        buffer = StringIO()
        self._yaml.dump(document.root, buffer)
        newline = preferred_text_newline(path)
        atomic_write_text(path, normalize_newlines(buffer.getvalue(), newline), newline="")


yaml_adapter = YamlAdapter()
