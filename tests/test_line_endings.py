from pathlib import Path

from prescribe.adapters.line import LineAdapter, LineDocument, preferred_newline
from prescribe.adapters.toml import TomlAdapter
from prescribe.adapters.yaml import YamlAdapter
from prescribe.document import Document


class TestPreferredNewline:
    def test_bat_files_force_crlf(self, tmp_path):
        assert preferred_newline(tmp_path / "setup.bat") == "\r\n"

    def test_cmd_files_force_crlf(self, tmp_path):
        assert preferred_newline(tmp_path / "launch.cmd") == "\r\n"

    def test_case_insensitive_extension(self, tmp_path):
        assert preferred_newline(tmp_path / "script.BAT") == "\r\n"

    def test_regular_file_defaults_to_lf(self, tmp_path):
        assert preferred_newline(tmp_path / ".env") == "\n"

    def test_detects_crlf_from_content(self, tmp_path):
        lines = ["export FOO=bar\r\n", "export BAZ=qux\r\n"]
        assert preferred_newline(tmp_path / ".env", lines) == "\r\n"

    def test_detects_lf_from_content(self, tmp_path):
        lines = ["export FOO=bar\n", "export BAZ=qux\n"]
        assert preferred_newline(tmp_path / ".env", lines) == "\n"

    def test_bat_overrides_content_detection(self, tmp_path):
        lines = ["echo hello\n"]
        assert preferred_newline(tmp_path / "setup.bat", lines) == "\r\n"

    def test_empty_lines_defaults_to_lf(self, tmp_path):
        assert preferred_newline(tmp_path / ".env", []) == "\n"


def _write_raw(path: Path, text: str) -> Path:
    """Write exact bytes without any newline translation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


class TestLineEndingPreservation:
    def test_lf_file_stays_lf_after_update(self, tmp_path):
        source = _write_raw(
            tmp_path / ".env",
            "# header\nunmanaged before\n# prescribe:begin managed\nold=1\n# prescribe:end managed\nunmanaged after\n",
        )

        adapter = LineAdapter()
        document = adapter.load(source)
        assert document.root.newline == "\n"
        document.root.ensure_block("managed", ["alpha=1", "beta=2"])

        target = tmp_path / ".env.out"
        adapter.dump(document, target)

        raw = target.read_bytes()
        assert b"\r\n" not in raw
        assert raw == (
            b"# header\n"
            b"unmanaged before\n"
            b"# prescribe:begin managed\n"
            b"alpha=1\n"
            b"beta=2\n"
            b"# prescribe:end managed\n"
            b"unmanaged after\n"
        )

    def test_crlf_file_stays_crlf_after_update(self, tmp_path):
        crlf_content = (
            "# header\r\n"
            "unmanaged before\r\n"
            "# prescribe:begin managed\r\n"
            "old=1\r\n"
            "# prescribe:end managed\r\n"
            "unmanaged after\r\n"
        )
        source = _write_raw(tmp_path / ".env", crlf_content)

        adapter = LineAdapter()
        document = adapter.load(source)
        assert document.root.newline == "\r\n"
        document.root.ensure_block("managed", ["alpha=1", "beta=2"])

        target = tmp_path / ".env.out"
        adapter.dump(document, target)

        raw = target.read_bytes()
        assert raw == (
            b"# header\r\n"
            b"unmanaged before\r\n"
            b"# prescribe:begin managed\r\n"
            b"alpha=1\r\n"
            b"beta=2\r\n"
            b"# prescribe:end managed\r\n"
            b"unmanaged after\r\n"
        )

    def test_new_file_defaults_to_lf(self, tmp_path):
        path = tmp_path / ".env"
        newline = preferred_newline(path)
        doc = LineDocument(path=path, newline=newline)
        doc.ensure_block("aliases", ["alias ll='ls -la'", "alias gs='git status'"])

        target = tmp_path / ".env.out"
        adapter = LineAdapter()
        adapter.dump(Document(path=doc.path, format=doc.format, root=doc), target)

        raw = target.read_bytes()
        assert b"\r\n" not in raw
        assert raw == (
            b"# prescribe:begin aliases\nalias ll='ls -la'\nalias gs='git status'\n# prescribe:end aliases\n"
        )

    def test_bat_file_gets_crlf_for_new_file(self, tmp_path):
        path = tmp_path / "setup.bat"
        newline = preferred_newline(path)
        assert newline == "\r\n"

        doc = LineDocument(path=path, newline=newline)
        doc.ensure_block("env", ["set HOME=%USERPROFILE%", "set PATH=%PATH%;C:\\tools"])

        target = tmp_path / "setup.out.bat"
        adapter = LineAdapter()
        adapter.dump(Document(path=doc.path, format=doc.format, root=doc), target)

        raw = target.read_bytes()
        assert raw == (
            b"# prescribe:begin env\r\nset HOME=%USERPROFILE%\r\nset PATH=%PATH%;C:\\tools\r\n# prescribe:end env\r\n"
        )

    def test_mixed_file_detects_first_crlf(self, tmp_path):
        source = _write_raw(tmp_path / ".profile", "export PATH=/usr/bin\r\nexport EDITOR=vim\n")

        adapter = LineAdapter()
        document = adapter.load(source)
        assert document.root.newline == "\r\n"

    def test_append_block_preserves_existing_crlf(self, tmp_path):
        source = _write_raw(tmp_path / ".zshrc", "export ZSH=$HOME/.oh-my-zsh\r\n")

        adapter = LineAdapter()
        document = adapter.load(source)
        document.root.ensure_block("aliases", ["alias ll='ls -la'"])

        target = tmp_path / ".zshrc.out"
        adapter.dump(document, target)

        raw = target.read_bytes()
        assert b"\r\n" in raw
        assert raw == (
            b"export ZSH=$HOME/.oh-my-zsh\r\n"
            b"# prescribe:begin aliases\r\n"
            b"alias ll='ls -la'\r\n"
            b"# prescribe:end aliases\r\n"
        )

    def test_existing_bat_file_preserves_crlf(self, tmp_path):
        source = _write_raw(
            tmp_path / "setup.bat",
            "@echo off\r\n# prescribe:begin env\r\nset OLD=1\r\n# prescribe:end env\r\n",
        )

        adapter = LineAdapter()
        document = adapter.load(source)
        assert document.root.newline == "\r\n"
        document.root.ensure_block("env", ["set HOME=%USERPROFILE%"])

        target = tmp_path / "setup.out.bat"
        adapter.dump(document, target)

        raw = target.read_bytes()
        assert raw == (b"@echo off\r\n# prescribe:begin env\r\nset HOME=%USERPROFILE%\r\n# prescribe:end env\r\n")


class TestStructuredLineEndingPreservation:
    def test_toml_lf_file_stays_lf_after_update(self, tmp_path):
        source = _write_raw(tmp_path / "config.toml", "title = 'hello'\ncount = 1\n")

        adapter = TomlAdapter()
        document = adapter.load(source)
        document.root["count"] = 2
        adapter.dump(document, source)

        raw = source.read_bytes()
        assert b"\r\n" not in raw
        assert b"count = 2\n" in raw

    def test_toml_crlf_file_stays_crlf_after_update(self, tmp_path):
        source = _write_raw(tmp_path / "config.toml", "title = 'hello'\r\ncount = 1\r\n")

        adapter = TomlAdapter()
        document = adapter.load(source)
        document.root["count"] = 2
        adapter.dump(document, source)

        raw = source.read_bytes()
        assert b"count = 2\r\n" in raw
        assert b"\n" not in raw.replace(b"\r\n", b"")

    def test_yaml_lf_file_stays_lf_after_update(self, tmp_path):
        source = _write_raw(tmp_path / "config.yaml", "title: hello\ncount: 1\n")

        adapter = YamlAdapter()
        document = adapter.load(source)
        document.root["count"] = 2
        adapter.dump(document, source)

        raw = source.read_bytes()
        assert b"\r\n" not in raw
        assert b"count: 2\n" in raw

    def test_yaml_crlf_file_stays_crlf_after_update(self, tmp_path):
        source = _write_raw(tmp_path / "config.yaml", "title: hello\r\ncount: 1\r\n")

        adapter = YamlAdapter()
        document = adapter.load(source)
        document.root["count"] = 2
        adapter.dump(document, source)

        raw = source.read_bytes()
        assert b"count: 2\r\n" in raw
        assert b"\n" not in raw.replace(b"\r\n", b"")
