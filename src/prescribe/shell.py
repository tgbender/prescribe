"""Shell block rendering."""

import json
import os
import shlex


def render_shell_block(
    *,
    shell_type: str,
    env_vars: dict[str, str],
    managed_block_id: str,
    path_prepend: list[str] | None = None,
    path_append: list[str] | None = None,
) -> list[str]:
    """Render a managed block with env var exports for a given shell type."""
    if not env_vars and path_prepend is None and path_append is None:
        return []

    renderer = _RENDERERS.get(shell_type)
    if renderer is None:
        raise ValueError(f"unsupported shell type: {shell_type!r}, expected one of {sorted(_RENDERERS.keys())}")
    if path_prepend is None and path_append is None:
        return renderer(env_vars=env_vars, managed_block_id=managed_block_id)
    lines = renderer(
        env_vars={key: value for key, value in env_vars.items() if key != "PATH"},
        managed_block_id=managed_block_id,
    )
    lines.extend(_render_path_edits(shell_type, path_prepend or [], path_append or []))
    return lines


def _render_path_edits(shell_type: str, prepend: list[str], append: list[str]) -> list[str]:
    """Relocate managed entries around the runtime PATH without baking in host state."""
    prepend = list(dict.fromkeys(prepend))
    append = [entry for entry in dict.fromkeys(append) if entry not in prepend]
    managed = prepend + append
    if not managed:
        return []
    if shell_type in {"bash", "zsh"}:
        patterns = "|".join(shlex.quote(entry) for entry in managed)
        return [
            'export PATH="$(',
            "  _prescribe_rest=${PATH-}",
            "  _prescribe_kept=",
            '  while [ -n "$_prescribe_rest" ]; do',
            "    _prescribe_entry=${_prescribe_rest%%:*}",
            '    case "$_prescribe_rest" in',
            "      *:*) _prescribe_rest=${_prescribe_rest#*:} ;;",
            "      *) _prescribe_rest= ;;",
            "    esac",
            '    case "$_prescribe_entry" in',
            f"      ''|{patterns}) ;;",
            "      *) _prescribe_kept=${_prescribe_kept:+$_prescribe_kept:}$_prescribe_entry ;;",
            "    esac",
            "  done",
            f"  _prescribe_result={shlex.quote(':'.join(prepend))}",
            '  if [ -n "$_prescribe_kept" ]; then',
            "    _prescribe_result=${_prescribe_result:+$_prescribe_result:}$_prescribe_kept",
            "  fi",
            *(
                [f"  _prescribe_result=${{_prescribe_result:+$_prescribe_result:}}{shlex.quote(':'.join(append))}"]
                if append
                else []
            ),
            '  printf "%s" "$_prescribe_result"',
            ')"',
        ]
    if shell_type == "pwsh":
        pre = ", ".join(_quote_pwsh(entry) for entry in prepend)
        post = ", ".join(_quote_pwsh(entry) for entry in append)
        return [
            "& {",
            f"  $pre = @({pre})",
            f"  $post = @({post})",
            "  $managed = @($pre) + @($post)",
            "  $kept = @($env:PATH -split [IO.Path]::PathSeparator | Where-Object { $_ -and $_ -notin $managed })",
            "  $env:PATH = (@($pre) + $kept + @($post)) -join [IO.Path]::PathSeparator",
            "}",
        ]
    if shell_type == "fish":
        pre = " ".join(_quote_fish(entry) for entry in prepend)
        post = " ".join(_quote_fish(entry) for entry in append)
        return [
            "begin",
            f"  set -l pre {pre}",
            f"  set -l post {post}",
            "  set -l kept",
            "  for entry in $PATH",
            '    if not contains -- "$entry" $pre $post',
            '      set -a kept "$entry"',
            "    end",
            "  end",
            "  set -gx PATH $pre $kept $post",
            "end",
        ]
    if shell_type == "xonsh":
        return [f"$PATH = {prepend!r} + [p for p in $PATH if p not in {managed!r}] + {append!r}"]
    if shell_type == "nu":
        return [
            f"$env.PATH = ({json.dumps(prepend)} ++ "
            f"($env.PATH | where {{|entry| $entry not-in {json.dumps(managed)}}}) ++ {json.dumps(append)})"
        ]
    if shell_type == "cmd":
        # Delayed expansion stays disabled so exclamation marks remain literal.
        lines = ["setlocal DisableDelayedExpansion", 'set "_prescribe_path=;%PATH%;"']
        for entry in managed:
            entry = entry.replace("%", "%%")
            lines.append(f'set "_prescribe_path=%_prescribe_path:;{entry};=;%"')
        lines.append('set "_prescribe_path=%_prescribe_path:~1,-1%"')
        if prepend:
            pre = ";".join(prepend).replace("%", "%%")
            lines.extend(
                [
                    "if defined _prescribe_path (",
                    f'  set "_prescribe_path={pre};%_prescribe_path%"',
                    f') else set "_prescribe_path={pre}"',
                ]
            )
        if append:
            post = ";".join(append).replace("%", "%%")
            lines.extend(
                [
                    "if defined _prescribe_path (",
                    f'  set "_prescribe_path=%_prescribe_path%;{post}"',
                    f') else set "_prescribe_path={post}"',
                ]
            )
        lines.append('endlocal & set "PATH=%_prescribe_path%"')
        return lines
    raise ValueError(f"unsupported shell type: {shell_type!r}")


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
