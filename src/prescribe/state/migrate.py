"""Execute migration ops against a SQLite connection."""

import sqlite3
from datetime import UTC, datetime

from prescribe.state.diff import (
    AddColumn,
    ArchiveAndRecreate,
    CreateIndex,
    CreateTable,
    DropIndex,
    Op,
    RebuildTable,
    diff_table,
)
from prescribe.state.introspect import get_pragma, list_user_tables, read_table
from prescribe.state.schema import Schema, Table


class _DryConn:
    """Stub that captures SQL without executing. Used for dry_run mode."""

    def __init__(self, log: list[str]) -> None:
        self._log = log

    def execute(self, sql: str) -> "_DryConn":
        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        return list()


def _ts() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _archive_name(conn: sqlite3.Connection | _DryConn, table: str) -> str:
    base = f"{table}__archived_{_ts()}"
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def _apply_pragmas(conn: sqlite3.Connection, pragmas: dict[str, str]) -> None:
    for k, v in pragmas.items():
        current = get_pragma(conn, k)
        # journal_mode returns the new mode; comparison is case-insensitive
        if current.lower() != v.lower():
            conn.execute(f"PRAGMA {k}={v}")


def _exec_op(conn: sqlite3.Connection | _DryConn, op: Op, log: list[str]) -> None:
    if isinstance(op, CreateTable):
        sql = op.table.ddl()
        log.append(sql)
        conn.execute(sql)

    elif isinstance(op, AddColumn):
        sql = f"ALTER TABLE {op.table} ADD COLUMN {op.column_ddl}"
        log.append(sql)
        conn.execute(sql)

    elif isinstance(op, CreateIndex):
        sql = op.index.ddl(op.table)
        log.append(sql)
        conn.execute(sql)

    elif isinstance(op, DropIndex):
        sql = f"DROP INDEX IF EXISTS {op.name}"
        log.append(sql)
        conn.execute(sql)

    elif isinstance(op, RebuildTable):
        _rebuild(conn, op.desired, op.preserved_columns, log)

    elif isinstance(op, ArchiveAndRecreate):
        archive = _archive_name(conn, op.desired.name)
        log.append(f"-- archive: {op.reason}")
        sql = f"ALTER TABLE {op.desired.name} RENAME TO {archive}"
        log.append(sql)
        conn.execute(sql)
        sql = op.desired.ddl()
        log.append(sql)
        conn.execute(sql)
        for idx in op.desired.indexes:
            sql = idx.ddl(op.desired.name)
            log.append(sql)
            conn.execute(sql)


def _rebuild(conn: sqlite3.Connection | _DryConn, desired: Table, preserved: tuple[str, ...], log: list[str]) -> None:
    """Rebuild via _new + swap. Preserved cols are copied; rest start NULL/default."""
    tmp = f"{desired.name}__new"
    # Build CREATE for tmp using desired schema but tmp name
    tmp_table = Table(name=tmp, columns=desired.columns, indexes=())
    sql = tmp_table.ddl()
    log.append(sql)
    conn.execute(sql)

    if preserved:
        cols_csv = ", ".join(preserved)
        sql = f"INSERT INTO {tmp} ({cols_csv}) SELECT {cols_csv} FROM {desired.name}"
        log.append(sql)
        conn.execute(sql)

    archive = _archive_name(conn, desired.name)
    sql = f"ALTER TABLE {desired.name} RENAME TO {archive}"
    log.append(sql)
    conn.execute(sql)
    sql = f"ALTER TABLE {tmp} RENAME TO {desired.name}"
    log.append(sql)
    conn.execute(sql)

    for idx in desired.indexes:
        sql = idx.ddl(desired.name)
        log.append(sql)
        conn.execute(sql)


def migrate(conn: sqlite3.Connection, schema: Schema, dry_run: bool = False, autocommit: bool = True) -> list[str]:
    """Reconcile DB to schema. Returns list of SQL statements executed (or planned)."""
    _apply_pragmas(conn, schema.pragmas)

    all_ops: list[Op] = []
    existing = set(list_user_tables(conn))
    for table in schema.tables:
        actual = read_table(conn, table.name) if table.name in existing else None
        all_ops.extend(diff_table(table, actual))

    log: list[str] = []
    if not all_ops:
        return log

    if dry_run:
        # Render without executing. For ops needing live conn (archive name
        # lookups), use a placeholder so we still get readable SQL.
        for op in all_ops:
            if isinstance(op, (RebuildTable, ArchiveAndRecreate)):
                log.append(
                    f"-- {type(op).__name__} on '{op.desired.name}' "
                    f"(SQL omitted in dry_run; archive name needs live DB)"
                )
            else:
                _exec_op(_DryConn(log), op, log)
        return log

    # Real run
    if autocommit:
        conn.execute("BEGIN IMMEDIATE")
    try:
        for op in all_ops:
            _exec_op(conn, op, log)
        if autocommit:
            conn.execute("COMMIT")
    except Exception:
        if autocommit:
            conn.execute("ROLLBACK")
        raise
    return log
