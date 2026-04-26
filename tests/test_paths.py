import sys
from pathlib import Path

import pytest

from prescribe.paths import config_dir, data_dir, default_state_path


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure no prescribe env vars leak between tests."""
    for key in ("PRESCRIBE_STATE", "PRESCRIBE_DATA_DIR", "PRESCRIBE_CONFIG_DIR", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(key, raising=False)


class TestDataDir:
    def test_default_on_linux(self, monkeypatch):
        monkeypatch.setattr("os.name", "posix")
        monkeypatch.delenv("HOME", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", "/home/user/.local/share")
        result = data_dir()
        assert result == Path("/home/user/.local/share/prescribe")

    def test_env_override(self, monkeypatch, tmp_path):
        custom = tmp_path / "my-data"
        monkeypatch.setenv("PRESCRIBE_DATA_DIR", str(custom))
        assert data_dir() == custom

    def test_xdg_data_home_respected(self, monkeypatch, tmp_path):
        xdg = tmp_path / "xdg-share"
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
        result = data_dir()
        assert result == xdg / "prescribe"


class TestConfigDir:
    def test_default_on_linux(self, monkeypatch):
        monkeypatch.setattr("os.name", "posix")
        monkeypatch.setenv("XDG_CONFIG_HOME", "/home/user/.config")
        result = config_dir()
        assert result == Path("/home/user/.config/prescribe")

    def test_env_override(self, monkeypatch, tmp_path):
        custom = tmp_path / "my-config"
        monkeypatch.setenv("PRESCRIBE_CONFIG_DIR", str(custom))
        assert config_dir() == custom

    def test_xdg_config_home_respected(self, monkeypatch, tmp_path):
        xdg = tmp_path / "xdg-config"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        result = config_dir()
        assert result == xdg / "prescribe"


class TestDefaultStatePath:
    def test_is_data_dir_plus_state_db(self, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", "/tmp/xdg-data")
        result = default_state_path()
        assert result == Path("/tmp/xdg-data/prescribe/state.db")

    def test_prescribe_state_env_override(self, monkeypatch, tmp_path):
        custom = tmp_path / "custom.db"
        monkeypatch.setenv("PRESCRIBE_STATE", str(custom))
        assert default_state_path() == custom

    def test_prescribe_state_takes_precedence_over_data_dir(self, monkeypatch, tmp_path):
        custom = tmp_path / "override.db"
        monkeypatch.setenv("PRESCRIBE_STATE", str(custom))
        monkeypatch.setenv("PRESCRIBE_DATA_DIR", str(tmp_path / "ignored"))
        assert default_state_path() == custom

    def test_data_dir_env_used_when_no_state_env(self, monkeypatch, tmp_path):
        custom_data = tmp_path / "my-data"
        monkeypatch.setenv("PRESCRIBE_DATA_DIR", str(custom_data))
        assert default_state_path() == custom_data / "state.db"


class TestMacOSXDGDefaults:
    def test_macos_sets_xdg_defaults_when_unset(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        result = data_dir()
        assert result == tmp_path / ".local" / "share" / "prescribe"

    def test_macos_preserves_existing_xdg(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "darwin")
        custom = tmp_path / "custom-xdg"
        monkeypatch.setenv("XDG_DATA_HOME", str(custom))
        result = data_dir()
        assert result == custom / "prescribe"

    def test_non_macos_does_not_set_xdg(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        # On Linux without XDG set, platformdirs falls back to ~/.local/share
        result = data_dir()
        assert "prescribe" in str(result)
