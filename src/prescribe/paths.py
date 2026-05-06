import os
import sys
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

_APP_NAME = "prescribe"

_ENV_STATE = "PRESCRIBE_STATE"
_ENV_DATA = "PRESCRIBE_DATA_DIR"
_ENV_CONFIG = "PRESCRIBE_CONFIG_DIR"


def _ensure_xdg_defaults() -> None:
    """Set XDG_DATA_HOME and XDG_CONFIG_HOME on macOS if not already set.

    platformdirs respects XDG env vars on all platforms.  On macOS it
    defaults to ~/Library/Application Support which is Apple's convention,
    but prescribe prefers the XDG spec (~/.local/share, ~/.config) for
    consistency across all Unix systems.
    """
    if sys.platform == "darwin":
        home = os.environ.get("HOME") or str(Path.home())
        os.environ.setdefault("XDG_DATA_HOME", f"{home}/.local/share")
        os.environ.setdefault("XDG_CONFIG_HOME", f"{home}/.config")


def _xdg_dir(env_name: str) -> Path | None:
    env = os.environ.get(env_name)
    if env:
        return Path(env) / _APP_NAME
    return None


def data_dir() -> Path:
    """Return the prescribe data directory.

    Resolution order:
      1. PRESCRIBE_DATA_DIR env var (if set)
      2. platformdirs (respects XDG_DATA_HOME on all platforms)
    """
    env = os.environ.get(_ENV_DATA)
    if env:
        return Path(env)
    _ensure_xdg_defaults()
    xdg = _xdg_dir("XDG_DATA_HOME")
    if xdg is not None:
        return xdg
    if sys.platform.startswith("linux"):
        return Path(os.environ.get("HOME") or Path.home()) / ".local" / "share" / _APP_NAME
    return Path(user_data_dir(_APP_NAME))


def config_dir() -> Path:
    """Return the prescribe config directory.

    Resolution order:
      1. PRESCRIBE_CONFIG_DIR env var (if set)
      2. platformdirs (respects XDG_CONFIG_HOME on all platforms)
    """
    env = os.environ.get(_ENV_CONFIG)
    if env:
        return Path(env)
    _ensure_xdg_defaults()
    xdg = _xdg_dir("XDG_CONFIG_HOME")
    if xdg is not None:
        return xdg
    if sys.platform.startswith("linux"):
        return Path(os.environ.get("HOME") or Path.home()) / ".config" / _APP_NAME
    return Path(user_config_dir(_APP_NAME))


def default_state_path() -> Path:
    """Return the default path for the prescribe state database.

    Resolution order:
      1. PRESCRIBE_STATE env var (if set)
      2. <data_dir>/state.db
    """
    env = os.environ.get(_ENV_STATE)
    if env:
        return Path(env)
    return data_dir() / "state.db"
