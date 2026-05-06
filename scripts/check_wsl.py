# /// script
# requires-python = ">=3.12"
# ///
from __future__ import annotations

import argparse
import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Invoke the prescribe check suite inside WSL2.")
    parser.add_argument("--distro", help="WSL distro name. Defaults to the configured default distro.")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout in seconds for each WSL-side check.")
    parser.add_argument("--target", default="~/tmp/prescribe-wsl-check", help="WSL-native scratch directory.")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Windows path to the repo. Defaults to this script's repo root.",
    )
    args = parser.parse_args()

    if platform.system() != "Windows":
        print("error: this invoker is intended to run from Windows", file=sys.stderr)
        return 2
    if shutil.which("wsl.exe") is None:
        print("error: wsl.exe was not found on PATH", file=sys.stderr)
        return 2

    repo = args.repo.resolve()
    runner = repo / "scripts" / "check_wsl_runner.py"
    if not runner.exists():
        print(f"error: WSL runner not found: {runner}", file=sys.stderr)
        return 2

    try:
        repo_wsl = _wslpath(repo, args.distro)
        runner_wsl = _wslpath(runner, args.distro)
        _check_wsl(args.distro)
        runner_command = " ".join(
            shlex.quote(part)
            for part in [
                "uv",
                "run",
                runner_wsl,
                "--source",
                repo_wsl,
                "--target",
                args.target,
                "--timeout",
                str(args.timeout),
            ]
        )
        command = ["wsl.exe", *_distro_args(args.distro), "--", "bash", "-lc", runner_command]
        print(f"copying {repo_wsl} to WSL target {args.target} and running checks")
        subprocess.run(command, check=True, env={**os.environ, "PYTHONUTF8": "1"})
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed with exit code {exc.returncode}: {_format_command(exc.cmd)}", file=sys.stderr)
        return exc.returncode or 1
    except subprocess.TimeoutExpired as exc:
        print(f"error: command timed out after {exc.timeout}s: {_format_command(exc.cmd)}", file=sys.stderr)
        return 124
    return 0


def _check_wsl(distro: str | None) -> None:
    subprocess.run(
        ["wsl.exe", *_distro_args(distro), "--", "sh", "-lc", 'test "$(uname -s)" = Linux && command -v uv'],
        check=True,
        timeout=30,
        env={**os.environ, "PYTHONUTF8": "1"},
    )


def _wslpath(path: Path, distro: str | None) -> str:
    result = subprocess.run(
        ["wsl.exe", *_distro_args(distro), "--", "wslpath", "-a", path.as_posix()],
        check=True,
        timeout=30,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    return result.stdout.strip()


def _distro_args(distro: str | None) -> list[str]:
    return [] if distro is None else ["-d", distro]


def _format_command(command: object) -> str:
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return str(command)


if __name__ == "__main__":
    raise SystemExit(main())
