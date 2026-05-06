# /// script
# requires-python = ">=3.12"
# ///
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

MARKER = ".prescribe-wsl-check"
DEFAULT_TARGET = "~/tmp/prescribe-wsl-check"

EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".uv-cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}
EXCLUDED_FILES = {".coverage"}

CHECKS = [
    ("pytest", ["uv", "run", "pytest", "-q"]),
    ("mypy", ["uv", "run", "mypy", "."]),
    ("ruff", ["mise", "exec", "--", "ruff", "check", "."]),
    ("format", ["mise", "exec", "--", "ruff", "format", "--check", "."]),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy prescribe into WSL-native scratch space and run checks.")
    parser.add_argument("--source", type=Path, required=True, help="Source checkout path visible from WSL.")
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        help=f"WSL-native scratch directory. Default: {DEFAULT_TARGET}",
    )
    parser.add_argument("--timeout", type=int, default=600, help="Timeout in seconds for each check.")
    args = parser.parse_args()

    source = args.source.resolve()
    target = _expand_target(args.target)
    if not (source / "pyproject.toml").exists():
        print(f"error: source does not look like prescribe checkout: {source}", file=sys.stderr)
        return 2
    if str(target).startswith("/mnt/"):
        print(f"error: target must be on WSL-native storage, not {target}", file=sys.stderr)
        return 2
    for tool in ("uv", "mise"):
        if shutil.which(tool) is None:
            print(f"error: required WSL tool not found on PATH: {tool}", file=sys.stderr)
            return 2

    try:
        _refresh_copy(source, target)
        _trust_mise_config(target, timeout=args.timeout)
        _run_checks(target, timeout=args.timeout)
    except OSError as exc:
        print(f"error: filesystem operation failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _expand_target(raw: str) -> Path:
    if raw == "~":
        return Path.home()
    if raw.startswith("~/"):
        return Path.home() / raw[2:]
    return Path(raw).expanduser().resolve()


def _refresh_copy(source: Path, target: Path) -> None:
    _validate_target(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        marker = target / MARKER
        if not marker.exists():
            raise OSError(f"refusing to overwrite unmarked target: {target}")
        shutil.rmtree(target)
    print(f"copying {source} -> {target}", flush=True)
    shutil.copytree(source, target, ignore=_ignore)
    (target / MARKER).write_text("managed by scripts/check_wsl_runner.py\n", encoding="utf-8")


def _validate_target(target: Path) -> None:
    home = Path.home().resolve()
    allowed_root = home / "tmp"
    resolved_parent = target.parent.resolve()
    if target.resolve() in {Path("/"), home, allowed_root}:
        raise OSError(f"refusing unsafe target: {target}")
    if allowed_root != resolved_parent and allowed_root not in resolved_parent.parents:
        raise OSError(f"target must be under {allowed_root}: {target}")


def _ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        path = Path(directory) / name
        if name in EXCLUDED_FILES or path.suffix == ".pyc" or (name in EXCLUDED_DIRS and path.is_dir()):
            ignored.add(name)
    return ignored


def _run_checks(repo: Path, *, timeout: int) -> None:
    for name, command in CHECKS:
        print(f"running {name}: {' '.join(command)}", flush=True)
        try:
            subprocess.run(command, cwd=repo, check=True, timeout=timeout, env={**os.environ, "PYTHONUTF8": "1"})
        except subprocess.CalledProcessError as exc:
            print(f"error: {name} failed with exit code {exc.returncode}", file=sys.stderr)
            raise SystemExit(exc.returncode or 1) from None
        except subprocess.TimeoutExpired:
            print(f"error: {name} timed out after {timeout}s", file=sys.stderr)
            raise SystemExit(124) from None


def _trust_mise_config(repo: Path, *, timeout: int) -> None:
    if not (repo / "mise.toml").exists():
        return
    print("trusting copied mise.toml", flush=True)
    subprocess.run(
        ["mise", "trust", "-y", "mise.toml"],
        cwd=repo,
        check=True,
        timeout=timeout,
        env={**os.environ, "PYTHONUTF8": "1"},
    )


if __name__ == "__main__":
    raise SystemExit(main())
