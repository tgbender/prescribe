import shutil
import tempfile
from pathlib import Path

from prescribe._util import sha256_bytes
from prescribe.encoding import TextEncodingError, decode_utf8_bytes
from prescribe.fs_safety import ensure_safe_displace_regular_file
from prescribe.paths import data_dir
from prescribe.state import StateStore


def backup_root(state_store: StateStore) -> Path:
    raw = str(state_store.path)
    if raw != ":memory:" and "file:" not in raw:
        return state_store.path.parent / "backups"
    return data_dir() / "backups"


def backup_asset_path(
    *,
    state_store: StateStore,
    run_id: int,
    original_path: Path,
    content_hash: bytes,
) -> Path:
    suffix = original_path.suffix
    stem = original_path.name[:80] or "asset"
    digest = content_hash.hex()
    return backup_root(state_store) / str(run_id) / f"{digest}--{stem}{suffix if not stem.endswith(suffix) else ''}"


def recovery_backup_path(
    *,
    state_store: StateStore,
    run_id: int,
    original_path: Path,
    content_hash: bytes,
) -> Path:
    suffix = original_path.suffix
    stem = original_path.name[:80] or "file"
    digest = content_hash.hex()
    name = f"{digest}--{stem}{suffix if not stem.endswith(suffix) else ''}"
    return backup_root(state_store) / "recovery" / str(run_id) / name


def copy_to_recovery_backup(
    *,
    state_store: StateStore,
    run_id: int,
    path: Path,
) -> tuple[Path, bytes, int, int | None, str | None]:
    payload = path.read_bytes()
    stat = path.stat()
    content_hash = sha256_bytes(payload)
    backup_path = recovery_backup_path(
        state_store=state_store,
        run_id=run_id,
        original_path=path,
        content_hash=content_hash,
    )
    backup_path = _allocate_backup_path(backup_path)
    shutil.copy2(path, backup_path)
    try:
        content_text = decode_utf8_bytes(payload, path=path)
    except TextEncodingError:
        content_text = None
    return backup_path, content_hash, stat.st_size, stat.st_mtime_ns, content_text


def move_to_backup(
    *,
    state_store: StateStore,
    run_id: int,
    path: Path,
) -> tuple[Path, bytes, int, int | None, str]:
    ensure_safe_displace_regular_file(
        path,
        operation="asset backup",
        max_bytes=path.stat().st_size,
        allow_binary=True,
    )
    file_type = "file"
    payload = path.read_bytes()
    size = path.stat().st_size
    mtime_ns = path.stat().st_mtime_ns
    content_hash = sha256_bytes(payload)
    backup_path = backup_asset_path(
        state_store=state_store,
        run_id=run_id,
        original_path=path,
        content_hash=content_hash,
    )
    backup_path = _allocate_backup_path(backup_path)
    shutil.move(str(path), str(backup_path))
    return backup_path, content_hash, size, mtime_ns, file_type


def _allocate_backup_path(path: Path) -> Path:
    """Reserve a distinct directory atomically, even for identical source metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the destination absent so a same-volume move can rename read-only
    # originals directly, without falling back to copy-and-unlink on Windows.
    return Path(tempfile.mkdtemp(dir=path.parent, prefix="entry-")) / path.name


def restore_backup(*, backup_path: Path, original_path: Path) -> None:
    original_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(backup_path), str(original_path))
