"""Shell block rendering."""

import os


def render_shell_block(*, shell_type: str, env_vars: dict[str, str], managed_block_id: str) -> list[str]:
    """Render a managed block with env var exports for a given shell type."""
    if not env_vars:
        return []

    renderer = _RENDERERS.get(shell_type)
    if renderer is None:
        raise ValueError(f"unsupported shell type: {shell_type!r}, expected one of {sorted(_RENDERERS.keys())}")
    return renderer(env_vars=env_vars, managed_block_id=managed_block_id)


def _render_xonsh(*, env_vars: dict[str, str], managed_block_id: str) -> list[str]:
    lines: list[str] = []
    path_value = env_vars.get("PATH")
    for name, value in sorted(env_vars.items()):
        if name == "PATH":
            continue
        lines.append(f"${name} = {_quote_xonsh(value)}")
    if path_value is not None:
        entries = _split_path(path_value)
        path_literal = ", ".join(_quote_xonsh(e) for e in entries)
        lines.append(f"$PATH = [{path_literal}]")
    return lines


def _render_posix(*, env_vars: dict[str, str], managed_block_id: str) -> list[str]:
    lines: list[str] = []
    for name, value in sorted(env_vars.items()):
        lines.append(f'export {name}="{_escape_posix(value)}"')
    return lines


def _render_fish(*, env_vars: dict[str, str], managed_block_id: str) -> list[str]:
    lines: list[str] = []
    path_value = env_vars.get("PATH")
    for name, value in sorted(env_vars.items()):
        if name == "PATH":
            continue
        lines.append(f"set -gx {name} {_quote_fish(value)}")
    if path_value is not None:
        entries = _split_path(path_value)
        path_literal = " ".join(_quote_fish(entry) for entry in entries)
        lines.append(f"set -gx PATH {path_literal}")
    return lines


def _render_nushell(*, env_vars: dict[str, str], managed_block_id: str) -> list[str]:
    lines: list[str] = []
    for name, value in sorted(env_vars.items()):
        lines.append(f'$env.{name} = "{_escape_posix(value)}"')
    return lines


def _render_pwsh(*, env_vars: dict[str, str], managed_block_id: str) -> list[str]:
    lines: list[str] = []
    path_value = env_vars.get("PATH")
    for name, value in sorted(env_vars.items()):
        if name == "PATH":
            continue
        lines.append(f"$env:{name} = {_quote_pwsh(value)}")
    if path_value is not None:
        entries = ", ".join(_quote_pwsh(entry) for entry in _split_windows_path(path_value))
        lines.append(f"$env:PATH = @({entries}) -join [IO.Path]::PathSeparator")
    return lines


def _render_cmd(*, env_vars: dict[str, str], managed_block_id: str) -> list[str]:
    lines: list[str] = []
    for name, value in sorted(env_vars.items()):
        lines.append(f'set "{name}={_escape_cmd(value)}"')
    return lines


# ── helpers ───────────────────────────────────────────────


_RENDERERS = {
    "xonsh": _render_xonsh,
    "bash": _render_posix,
    "zsh": _render_posix,
    "fish": _render_fish,
    "nu": _render_nushell,
    "pwsh": _render_pwsh,
    "cmd": _render_cmd,
}


def _split_path(path_value: str) -> list[str]:
    return [p for p in path_value.split(os.pathsep) if p]


def _split_windows_path(path_value: str) -> list[str]:
    if ";" not in path_value:
        return _split_path(path_value)
    return [p for p in path_value.split(";") if p]


def _quote_xonsh(value: str) -> str:
    if value.startswith("!") or value.startswith("$("):
        return value  # leave command substitutions as-is
    return repr(value)


def _quote_fish(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _quote_pwsh(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _escape_cmd(value: str) -> str:
    return (
        value.replace("%", "%%")
        .replace("^", "^^")
        .replace("&", "^&")
        .replace("|", "^|")
        .replace("<", "^<")
        .replace(">", "^>")
    )


def _escape_posix(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
