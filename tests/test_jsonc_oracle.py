from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from prescribe.adapters.jsonc import JsoncAdapter

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
