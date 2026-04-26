from pathlib import Path

import json5

from prescribe.atomic import atomic_write_text
from prescribe.document import Document


class Json5Adapter:
    format_name = "json5"

    def load(self, path: Path) -> Document:
        root = json5.loads(path.read_text(encoding="utf-8"))
        return Document(path=path, format=self.format_name, root=root)

    def dump(self, document: Document, path: Path) -> None:
        atomic_write_text(
            path,
            json5.dumps(document.root, indent=2, quote_keys=True, trailing_commas=True),
        )


json5_adapter = Json5Adapter()
