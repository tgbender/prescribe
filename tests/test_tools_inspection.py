"""Tests for read-only tool inspection."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from prescribe import inspect_tool_paths


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_uv_targeted_lookup_uses_tool_bin_override(tmp_path: Path) -> None:
    home = tmp_path / "home"
    bin_dir = tmp_path / "uv-bin"
    tool_dir = tmp_path / "uv-tools"
    _exe(bin_dir / "ruff.exe")
    (tool_dir / "ruff").mkdir(parents=True)
    env = {
        "USERPROFILE": str(home),
        "PATH": str(bin_dir),
        "PATHEXT": ".EXE;.CMD",
        "UV_TOOL_BIN_DIR": str(bin_dir),
        "UV_TOOL_DIR": str(tool_dir),
    }

    report = inspect_tool_paths(["ruff"], managers=["uv"], env=env, home=home, platform="win32")

    assert [tool.name for tool in report.installed] == ["ruff"]
    assert report.tools["ruff"][0].path == bin_dir / "ruff.exe"
    assert report.tools["ruff"][0].active is True
    assert report.sources[0].kind == "tool-bin"


def test_mise_reads_manifest_for_intentional_installs(tmp_path: Path) -> None:
    home = tmp_path / "home"
    data = tmp_path / "data"
    installs = data / "mise" / "installs"
    (installs / "ripgrep" / "14.0.0").mkdir(parents=True)
    (installs / ".mise-installs.toml").write_text('[ripgrep]\nshort = "ripgrep"\n', encoding="utf-8")
    shims = data / "mise" / "shims"
    _exe(shims / "rg")
    env = {"HOME": str(home), "PATH": str(shims), "XDG_DATA_HOME": str(data)}

    report = inspect_tool_paths(["rg"], managers=["mise"], env=env, home=home, platform="linux")

    assert [(tool.name, tool.scope) for tool in report.installed] == [("ripgrep", "intentional")]
    assert report.tools["rg"][0].source == "mise"
    assert report.tools["rg"][0].active is True


def test_targeted_lookup_attributes_shims_to_installed_executables(tmp_path: Path) -> None:
    home = tmp_path / "home"
    data = tmp_path / "data"
    installs = data / "mise" / "installs"
    ripgrep_bin = installs / "ripgrep" / "15.1.0" / "pkg"
    _exe(ripgrep_bin / "rg.exe")
    (installs / ".mise-installs.toml").write_text('[ripgrep]\nshort = "ripgrep"\n', encoding="utf-8")
    shims = data / "mise" / "shims"
    _exe(shims / "rg.exe")
    env = {"USERPROFILE": str(home), "PATH": str(shims), "XDG_DATA_HOME": str(data), "PATHEXT": ".EXE;.CMD"}

    report = inspect_tool_paths(["rg"], managers=["mise"], env=env, home=home, platform="win32")

    assert report.tools["rg"][0].installed_name == "ripgrep"
    assert report.tools["rg"][0].scope == "intentional"


def test_targeted_lookup_does_not_attribute_path_entries_by_executable_name(tmp_path: Path) -> None:
    home = tmp_path / "home"
    data = tmp_path / "data"
    installs = data / "mise" / "installs"
    _exe(installs / "mypy" / "1.0" / "bin" / "python.exe")
    (installs / ".mise-installs.toml").write_text('[mypy]\nshort = "mypy"\n', encoding="utf-8")
    path_dir = tmp_path / "path"
    _exe(path_dir / "python.exe")
    env = {"USERPROFILE": str(home), "PATH": str(path_dir), "XDG_DATA_HOME": str(data), "PATHEXT": ".EXE;.CMD"}

    report = inspect_tool_paths(["python"], managers=["mise"], env=env, home=home, platform="win32")

    assert report.tools["python"][0].source == "path[0]"
    assert report.tools["python"][0].installed_name is None
    assert report.tools["python"][0].scope == "active"


def test_brew_filters_transitive_dependencies_by_default(tmp_path: Path) -> None:
    home = tmp_path / "home"
    prefix = tmp_path / "homebrew"
    bin_dir = prefix / "bin"
    _exe(bin_dir / "fd")
    _exe(bin_dir / "libevent-tool")
    fd = prefix / "Cellar" / "fd" / "10.0.0"
    dep = prefix / "Cellar" / "libevent" / "2.1.0"
    fd.mkdir(parents=True)
    dep.mkdir(parents=True)
    (fd / "INSTALL_RECEIPT.json").write_text('{"installed_on_request": true}', encoding="utf-8")
    (dep / "INSTALL_RECEIPT.json").write_text('{"installed_on_request": false}', encoding="utf-8")
    env = {"HOME": str(home), "PATH": str(bin_dir), "HOMEBREW_PREFIX": str(prefix)}

    report = inspect_tool_paths(["fd"], managers=["brew"], env=env, home=home, platform="darwin")
    with_deps = inspect_tool_paths(
        ["fd"], managers=["brew"], env=env, home=home, platform="darwin", include_transitive=True
    )

    assert [(tool.name, tool.scope) for tool in report.installed] == [("fd", "intentional")]
    assert [(tool.name, tool.scope) for tool in with_deps.installed] == [
        ("fd", "intentional"),
        ("libevent", "dependency"),
    ]
    assert "fd" in report.tools


def test_scoop_resolves_local_roots_and_shims(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "scoop"
    shims = root / "shims"
    (root / "apps" / "ripgrep" / "current").mkdir(parents=True)
    _exe(shims / "rg.exe")
    env = {
        "USERPROFILE": str(home),
        "PATH": str(shims),
        "PATHEXT": ".EXE;.CMD",
        "SCOOP": str(root),
        "CommonApplicationData": str(tmp_path / "ProgramData"),
    }

    report = inspect_tool_paths(["rg"], managers=["scoop"], env=env, home=home, platform="win32")

    assert [(tool.name, tool.manager, tool.scope) for tool in report.installed] == [("ripgrep", "scoop", "intentional")]
    assert report.tools["rg"][0].path == shims / "rg.exe"
    assert report.tools["rg"][0].active is True


def test_pnpm_uses_home_bin_and_lists_global_packages(tmp_path: Path) -> None:
    home = tmp_path / "home"
    pnpm_home = tmp_path / "pnpm"
    bin_dir = pnpm_home / "bin"
    _exe(bin_dir / "typescript")
    (pnpm_home / "global" / "node_modules" / "typescript").mkdir(parents=True)
    (pnpm_home / "global" / "node_modules" / "@scope" / "tool").mkdir(parents=True)
    env = {"HOME": str(home), "PATH": str(bin_dir), "PNPM_HOME": str(pnpm_home)}

    report = inspect_tool_paths(["typescript"], managers=["pnpm"], env=env, home=home, platform="linux")

    assert sorted((tool.name, tool.scope) for tool in report.installed) == [
        ("@scope/tool", "intentional"),
        ("typescript", "intentional"),
    ]
    assert report.tools["typescript"][0].active is True


def test_targeted_lookup_does_not_scan_unrequested_names(tmp_path: Path) -> None:
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    _exe(bin_dir / "wanted")
    _exe(bin_dir / "other")
    env = {"HOME": str(home), "PATH": str(bin_dir), "UV_TOOL_BIN_DIR": str(bin_dir)}

    report = inspect_tool_paths(["wanted"], managers=["uv"], env=env, home=home, platform="linux")

    assert set(report.tools) == {"wanted"}


def test_batch_lookup_reports_duplicates_and_active_path_order(tmp_path: Path) -> None:
    home = tmp_path / "home"
    first = tmp_path / "first"
    second = tmp_path / "second"
    _exe(first / "uv")
    _exe(second / "uv")
    env = {"HOME": str(home), "PATH": os.pathsep.join([str(first), str(second)]), "UV_TOOL_BIN_DIR": str(second)}

    report = inspect_tool_paths(["uv"], managers=["uv"], env=env, home=home, platform="linux")

    assert len(report.duplicates["uv"]) == 2
    assert [candidate.active for candidate in report.duplicates["uv"]] == [False, True]
