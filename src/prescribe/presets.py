"""Convenience helpers for managing multiple spec files.

Typical usage from a dotfiles repo:

    from prescribe import presets
    presets.apply_all("~/dotfiles/prescribe")
"""

from __future__ import annotations

import os
from pathlib import Path

from prescribe.core.result import OrchestrationResult
from prescribe.orchestrator import Orchestrator
from prescribe.paths import config_dir, default_state_path
from prescribe.spec import SpecLoader
from prescribe.state import ManagedRecord, StateStore

_DEFAULT_SPEC_DIR_ENV = "PRESCRIBE_SPEC_DIR"


def default_spec_dir() -> Path:
    env = os.environ.get(_DEFAULT_SPEC_DIR_ENV)
    if env:
        return Path(env).expanduser()
    return config_dir() / "specs"


def discover_specs(directory: Path) -> list[Path]:
    """Return sorted list of all *.toml files in *directory*."""
    if not directory.exists():
        return []
    return sorted(directory.glob("*.toml"))


def _make_orchestrator(state_store: StateStore | None = None) -> Orchestrator:
    store = state_store if state_store is not None else StateStore(default_state_path())
    store.initialize()
    return Orchestrator(state_store=store)


class Presets:
    """Discover and run multiple spec files from a directory."""

    def __init__(
        self,
        *,
        state_store: StateStore | None = None,
        loader: SpecLoader | None = None,
    ) -> None:
        self._store = state_store if state_store is not None else StateStore(default_state_path())
        self._loader = loader if loader is not None else SpecLoader()

    def list_specs(self, directory: Path | str | None = None) -> list[Path]:
        """List discovered specs without applying."""
        path = _resolve_dir(directory)
        return discover_specs(path)

    def apply_all(
        self,
        directory: Path | str | None = None,
        *,
        dry_run: bool = False,
        tags: set[str] | None = None,
        skip_tags: set[str] | None = None,
        diff: bool = False,
    ) -> list[tuple[Path, list[OrchestrationResult]]]:
        """Discover and apply (or dry-run) every *.toml spec in *directory*."""
        path = _resolve_dir(directory)
        specs = discover_specs(path)
        if not specs:
            return []

        self._store.initialize()
        orch = Orchestrator(state_store=self._store)
        results: list[tuple[Path, list[OrchestrationResult]]] = []
        for spec_path in specs:
            spec = self._loader.load(spec_path)
            spec_results = orch.run(
                spec,
                dry_run=dry_run,
                tags=tags,
                skip_tags=skip_tags,
                diff=diff,
            )
            results.append((spec_path, spec_results))
        return results

    def status(
        self,
        directory: Path | str | None = None,
        *,
        tags: set[str] | None = None,
        skip_tags: set[str] | None = None,
        diff: bool = False,
    ) -> list[tuple[Path, list[OrchestrationResult]]]:
        """Show sync status for all discovered specs without making changes."""
        return self.apply_all(
            directory,
            dry_run=True,
            tags=tags,
            skip_tags=skip_tags,
            diff=diff,
        )

    def list_managed(self) -> list[ManagedRecord]:
        """List all config files managed by prescribe."""
        self._store.initialize()
        return self._store.list_managed()


# ── module-level convenience ──────────────────────────────


def apply_all(
    directory: Path | str | None = None,
    *,
    dry_run: bool = False,
    tags: set[str] | None = None,
    skip_tags: set[str] | None = None,
    diff: bool = False,
    state_store: StateStore | None = None,
) -> list[tuple[Path, list[OrchestrationResult]]]:
    """Apply all discovered *.toml specs in *directory*.

    Args:
        directory: Path to directory containing spec files.
            Defaults to PRESCRIBE_SPEC_DIR env var, then ~/.config/prescribe/specs.
        dry_run: Preview without writing if True.
        tags: Only apply targets with these tags.
        skip_tags: Skip targets with these tags.
        diff: Include unified diffs.
        state_store: Optional custom StateStore.
    """
    return Presets(state_store=state_store).apply_all(
        directory,
        dry_run=dry_run,
        tags=tags,
        skip_tags=skip_tags,
        diff=diff,
    )


def status(
    directory: Path | str | None = None,
    *,
    tags: set[str] | None = None,
    skip_tags: set[str] | None = None,
    diff: bool = False,
    state_store: StateStore | None = None,
) -> list[tuple[Path, list[OrchestrationResult]]]:
    """Show sync status for all discovered specs without making changes."""
    return Presets(state_store=state_store).status(
        directory,
        tags=tags,
        skip_tags=skip_tags,
        diff=diff,
    )


def list_specs(directory: Path | str | None = None) -> list[Path]:
    """List discovered spec files without applying."""
    return Presets().list_specs(directory)


def _resolve_dir(directory: Path | str | None) -> Path:
    if directory is None:
        return default_spec_dir()
    return Path(directory).expanduser()
