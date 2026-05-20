from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from prescribe.adapters.jsonc import JsoncAdapter
from prescribe.jsonc import modify_text

ORACLE_FIXTURE = Path("tests/fixtures/vscode_settings.jsonc")
ORACLE_ENABLED = os.getenv("CONFIG_HELPER_RUN_JSONC_ORACLE") == "1"


@pytest.mark.skipif(
    not ORACLE_ENABLED,
    reason="JSONC oracle is gated; set CONFIG_HELPER_RUN_JSONC_ORACLE=1 to enable",
)
def test_jsonc_oracle_matches_bunx_output(tmp_path: Path) -> None:
    if shutil.which("mise") is None:
        pytest.skip("mise is not available")

    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / ORACLE_FIXTURE
    target = tmp_path / "settings.out.jsonc"

    adapter = JsoncAdapter()
    document = adapter.load(source)
    document.root["files.autoSave"] = "onFocusChange"
    document.root["files.autoSaveDelay"] = 2500
    document.root["workbench.editor.revealIfOpen"] = False
    document.root["notebook.editorOptionsCustomizations"]["editor.indentSize"] = 2
    document.root["github.copilot.nextEditSuggestions.enabled"] = False
    document.root["workbench.colorCustomizations"]["gitDecoration.untrackedResourceForeground"] = "#ff4444"
    adapter.dump(document, target)

    env = os.environ.copy()
    env.setdefault("NODE_JSONC_PARSER_DIR", "/Users/travis/projects/node-jsonc-parser")
    result = subprocess.run(
        ["mise", "run", "oracle-jsonc"],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert target.read_text(encoding="utf-8") == result.stdout


def _node_jsonc_parser_dir() -> Path:
    return Path(os.getenv("NODE_JSONC_PARSER_DIR", "/Users/travis/projects/node-jsonc-parser"))


def _node_jsonc_modify(tmp_path: Path, text: str, edits: list[dict[str, object]]) -> str:
    if shutil.which("bun") is None:
        pytest.skip("bun is not available")
    parser_dir = _node_jsonc_parser_dir()
    if not parser_dir.exists():
        pytest.skip(f"node-jsonc-parser checkout is not available: {parser_dir}")

    source = tmp_path / "source.jsonc"
    edit_file = tmp_path / "edits.json"
    script = tmp_path / "jsonc_oracle.ts"
    source.write_text(text, encoding="utf-8")
    edit_file.write_text(json.dumps(edits), encoding="utf-8")
    script.write_text(
        """
import { readFileSync } from "node:fs";

const sourcePath = process.argv[2];
const editsPath = process.argv[3];
const nodeJsoncRoot = process.env.NODE_JSONC_PARSER_DIR ?? "/Users/travis/projects/node-jsonc-parser";
const jsoncParserUrl = new URL("src/main.ts", `file://${nodeJsoncRoot.replace(/\\\\/g, "/")}/`);
const { applyEdits, modify } = await import(jsoncParserUrl.href);

const formattingOptions = {
  insertSpaces: true,
  tabSize: 2,
  eol: "\\n",
  keepLines: true,
};

let text = readFileSync(sourcePath, "utf8");
const edits = JSON.parse(readFileSync(editsPath, "utf8"));
for (const edit of edits) {
  text = applyEdits(text, modify(text, edit.path, edit.value, { formattingOptions }));
}
process.stdout.write(text);
""".lstrip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bun", str(script), str(source), str(edit_file)],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


@pytest.mark.skipif(
    not ORACLE_ENABLED,
    reason="JSONC oracle is gated; set CONFIG_HELPER_RUN_JSONC_ORACLE=1 to enable",
)
def test_jsonc_nested_object_insertion_matches_node_jsonc_parser_oracle(tmp_path: Path) -> None:
    source = (
        "{\n"
        "  // keep root\n"
        '  "editor": {\n'
        '    "fontSize": 14\n'
        "  }\n"
        "}\n"
    )

    local = modify_text(source, ["prescribe"], {"dogfood": {"enabled": True}})
    oracle = _node_jsonc_modify(
        tmp_path,
        source,
        [{"path": ["prescribe"], "value": {"dogfood": {"enabled": True}}}],
    )

    assert local == oracle
