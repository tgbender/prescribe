import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const repoRoot = process.cwd();
const sourcePath = resolve(repoRoot, "tests/fixtures/vscode_settings.jsonc");
const nodeJsoncRoot = process.env.NODE_JSONC_PARSER_DIR ?? "/Users/travis/projects/node-jsonc-parser";
const jsoncParserUrl = new URL("src/main.ts", `file://${nodeJsoncRoot.replace(/\\/g, "/")}/`);
const { applyEdits, modify } = await import(jsoncParserUrl.href);

const formattingOptions = {
  insertSpaces: true,
  tabSize: 2,
  eol: "\n",
  keepLines: true,
};

let text = readFileSync(sourcePath, "utf8");
text = applyEdits(text, modify(text, ["files.autoSave"], "onFocusChange", { formattingOptions }));
text = applyEdits(text, modify(text, ["files.autoSaveDelay"], 2500, { formattingOptions }));
text = applyEdits(text, modify(text, ["workbench.editor.revealIfOpen"], false, { formattingOptions }));
text = applyEdits(text, modify(text, ["notebook.editorOptionsCustomizations", "editor.indentSize"], 2, { formattingOptions }));
text = applyEdits(text, modify(text, ["github.copilot.nextEditSuggestions.enabled"], false, { formattingOptions }));
text = applyEdits(text, modify(text, ["workbench.colorCustomizations", "gitDecoration.untrackedResourceForeground"], "#ff4444", { formattingOptions }));
process.stdout.write(text);
