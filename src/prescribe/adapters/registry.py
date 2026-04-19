from __future__ import annotations

from pathlib import Path

from prescribe.adapters.json5 import json5_adapter
from prescribe.adapters.jsonc import jsonc_adapter
from prescribe.adapters.line import line_adapter
from prescribe.adapters.toml import toml_adapter
from prescribe.adapters.yaml import yaml_adapter
from prescribe.document import Adapter

EXACT_NAME_ADAPTERS: dict[str, Adapter] = {
    ".env": line_adapter,
    ".gitattributes": line_adapter,
    ".gitignore": line_adapter,
    ".npmrc": line_adapter,
    ".editorconfig": line_adapter,
}

SUFFIX_ADAPTERS: dict[str, Adapter] = {
    ".toml": toml_adapter,
    ".yaml": yaml_adapter,
    ".yml": yaml_adapter,
    ".json5": json5_adapter,
    ".json": jsonc_adapter,
    ".jsonc": jsonc_adapter,
    ".code-workspace": jsonc_adapter,
    ".conf": line_adapter,
    ".rc": line_adapter,
    ".ini": line_adapter,
}


FORMAT_ADAPTERS: dict[str, Adapter] = {
    "toml": toml_adapter,
    "yaml": yaml_adapter,
    "json5": json5_adapter,
    "jsonc": jsonc_adapter,
    "line": line_adapter,
}


def adapter_for_format(fmt: str) -> Adapter:
    try:
        return FORMAT_ADAPTERS[fmt]
    except KeyError as exc:
        raise ValueError(f"unsupported format: {fmt}") from exc


def adapter_for_path(path: Path, fmt: str | None = None) -> Adapter:
    if fmt is not None:
        return adapter_for_format(fmt)

    name = path.name.lower()
    if name in EXACT_NAME_ADAPTERS:
        return EXACT_NAME_ADAPTERS[name]

    try:
        return SUFFIX_ADAPTERS[path.suffix.lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported config file type: {path}") from exc
