"""Schema migration: diff ORM models vs actual DB, apply operations.

Reads the canonical schema from :class:`prescribe.state.models.Base.metadata`
and reconciles it against the live SQLite database.  No version files, no
alembic — change a model, restart, and migrate() auto-applies.

Adapted from the xonsh db/migrate.py approach (which was itself adapted
from an earlier version of this module).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column
from sqlalchemy import Index as SAIndex
from sqlalchemy import Table as SATable

# ── Operation types ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CreateTable:
    name: str
    sql: str


@dataclass(frozen=True)
class AddColumn:
    table: str
    sql: str


@dataclass(frozen=True)
class CreateIndex:
    table: str
    sql: str


@dataclass(frozen=True)
class DropIndex:
    name: str


@dataclass(frozen=True)
class RebuildTable:
    table: str
    reason: str


Op = CreateTable | AddColumn | CreateIndex | DropIndex | RebuildTable


# ── Helpers ───────────────────────────────────────────────────────────────────


def _ts() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _archive_name(conn: sqlite3.Connection, table: str) -> str:
    base = f"{table}__archived_{_ts()}"
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def _sa_type_to_sqlite(col_type: str) -> str:
    """Map SQLAlchemy type string to SQLite type name."""
    t = col_type.upper()
    if "VARCHAR" in t or "TEXT" in t:
        return "TEXT"
    if "INTEGER" in t or "INT" in t or "BIGINT" in t:
        return "INTEGER"
    if "FLOAT" in t or "REAL" in t or "DECIMAL" in t:
        return "REAL"
    if "BOOLEAN" in t:
        return "INTEGER"
    if "BLOB" in t or "BINARY" in t:
        return "BLOB"
    return "TEXT"


def _col_ddl(name: str, col: Column[Any]) -> str:
    """Generate column DDL from a SQLAlchemy Column."""
    col_type = str(col.type)
    parts = [name, _sa_type_to_sqlite(col_type)]
    if not col.nullable:
        parts.append("NOT NULL")
    if col.primary_key:
        parts.append("PRIMARY KEY")
    if col.foreign_keys:
        for fk in col.foreign_keys:
            parts.append(f"REFERENCES {fk.column.table.name}({fk.column.name}) ON DELETE CASCADE")
    return " ".join(parts)


def _table_ddl(table: SATable) -> str:
    """Generate full CREATE TABLE DDL from a SQLAlchemy Table."""
    col_defs = []
    compound_pk = []
    for col in table.columns:
        col_defs.append(_col_ddl(col.name, col))
        if len(table.primary_key.columns) > 1 and col.primary_key:
            compound_pk.append(col.name)

    if compound_pk:
        col_defs.append(f"PRIMARY KEY ({', '.join(compound_pk)})")

    body = ",\n  ".join(col_defs)
    if body.rstrip().endswith(","):
        body = body.rstrip(",")
    return f"CREATE TABLE IF NOT EXISTS {table.name} (\n  {body}\n)"


def _index_ddl(table_name: str, index: SAIndex) -> str:
    """Generate CREATE INDEX DDL from a SQLAlchemy Index."""
    u = "UNIQUE " if index.unique else ""
    cols = ", ".join(c.name for c in index.columns)
    return f"CREATE {u}INDEX IF NOT EXISTS {index.name} ON {table_name}({cols})"


# ── Introspection ─────────────────────────────────────────────────────────────


def _list_user_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return [r[0] for r in rows]


def _get_pragma(conn: sqlite3.Connection, pragma: str) -> str:
    row = conn.execute(f"PRAGMA {pragma}").fetchone()
    return str(row[0]) if row else ""


def _read_table(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    """Return a lightweight dict of column info, or None if table missing."""
    cols = conn.execute(f"PRAGMA table_info('{name}')").fetchall()
    if not cols:
        return None
    # (cid, name, type, notnull, dflt_value, pk)
    col_map: dict[str, dict[str, Any]] = {}
    for r in cols:
        col_map[r[1]] = {
            "name": r[1],
            "type": r[2].upper() if r[2] else "",
            "notnull": bool(r[3]),
            "pk": bool(r[5]),
        }

    actual_pk = tuple(c["name"] for c in col_map.values() if c["pk"])

    # Indexes — skip sqlite_autoindex_*
    idx_rows = conn.execute(f"PRAGMA index_list('{name}')").fetchall()
    indexes: dict[str, tuple[str, ...]] = {}
    for ir in idx_rows:
        iname = ir[1]
        if iname.startswith("sqlite_autoindex_"):
            continue
        info_rows = conn.execute(f"PRAGMA index_info('{iname}')").fetchall()
        idx_cols = tuple(r[2] for r in info_rows if r[2] is not None)
        if idx_cols:
            indexes[iname] = idx_cols

    return {"columns": col_map, "pk": actual_pk, "indexes": indexes}


# ── Diff ──────────────────────────────────────────────────────────────────────


def _types_compatible(desired: str, actual: str) -> bool:
    d = desired.upper()
    a = actual.upper()
    if d == "INTEGER" and a == "INTEGER":
        return True
    if d in ("TEXT", "VARCHAR") and a in ("TEXT", "VARCHAR", ""):
        return True
    if d in ("REAL", "FLOAT") and a in ("REAL", "FLOAT", ""):
        return True
    return d == a


def _diff_table(desired: SATable, actual: dict[str, Any] | None) -> list[Op]:
    if actual is None:
        result: list[Op] = [CreateTable(desired.name, _table_ddl(desired))]
        for idx in desired.indexes:
            if idx.name:
                result.append(CreateIndex(desired.name, _index_ddl(desired.name, idx)))
        return result

    desired_cols = {c.name: c for c in desired.columns}
    actual_cols = actual["columns"]
    d_names = set(desired_cols)
    a_names = set(actual_cols)

    # Type or PK change on shared columns → rebuild
    for name in d_names & a_names:
        d = desired_cols[name]
        a = actual_cols[name]
        d_type = _sa_type_to_sqlite(str(d.type))
        if not _types_compatible(d_type, a["type"]):
            return [RebuildTable(desired.name, f"type change on '{name}' ({a['type']} → {d_type})")]
        if d.primary_key != a["pk"]:
            return [RebuildTable(desired.name, f"PK change on '{name}'")]

    # PK composition change → rebuild
    desired_pk = tuple(c.name for c in desired.columns if c.primary_key)
    if desired_pk != actual["pk"]:
        return [RebuildTable(desired.name, "primary key composition changed")]

    added = d_names - a_names
    removed = a_names - d_names

    if removed:
        return [RebuildTable(desired.name, f"columns removed: {', '.join(sorted(removed))}")]

    ops: list[Op] = []
    for name in sorted(added, key=lambda n: list(d_names).index(n)):
        col = desired_cols[name]
        if col.primary_key:
            return [RebuildTable(desired.name, f"new PK column '{name}' — need rebuild")]
        ops.append(AddColumn(desired.name, f"ALTER TABLE {desired.name} ADD COLUMN {_col_ddl(name, col)}"))

    # Index diff
    desired_idx = {i.name: i for i in desired.indexes if i.name}
    actual_idx = actual["indexes"]
    for iname, a_cols in actual_idx.items():
        if iname not in desired_idx:
            ops.append(DropIndex(iname))
        else:
            d_idx = desired_idx[iname]
            d_cols = tuple(c.name for c in d_idx.columns)
            if d_cols != a_cols or d_idx.unique != (iname.startswith("uq_") or False):
                ops.append(DropIndex(iname))
                ops.append(CreateIndex(desired.name, _index_ddl(desired.name, d_idx)))
    for iname in desired_idx:
        if iname not in actual_idx:
            ops.append(CreateIndex(desired.name, _index_ddl(desired.name, desired_idx[iname])))

    return ops


# ── Apply ─────────────────────────────────────────────────────────────────────


def _exec_op(conn: sqlite3.Connection, op: Op, log: list[str]) -> None:
    if isinstance(op, (CreateTable, AddColumn, CreateIndex)):
        log.append(op.sql)
        conn.execute(op.sql)
    elif isinstance(op, DropIndex):
        sql = f"DROP INDEX IF EXISTS {op.name}"
        log.append(sql)
        conn.execute(sql)
    elif isinstance(op, RebuildTable):
        _rebuild(conn, op, log)


def _rebuild(conn: sqlite3.Connection, op: RebuildTable, log: list[str]) -> None:
    archive = _archive_name(conn, op.table)
    log.append(f"-- rebuild: {op.reason}")
    conn.execute(f"ALTER TABLE {op.table} RENAME TO {archive}")
    log.append(f"-- {op.table} archived to {archive}")

    from prescribe.state.models import Base

    table = Base.metadata.tables.get(op.table)
    if table is None:
        raise RuntimeError(f"Table '{op.table}' not found in ORM metadata")
    conn.execute(_table_ddl(table))
    log.append(f"-- {op.table} recreated")
    for idx in table.indexes:
        if idx.name:
            conn.execute(_index_ddl(op.table, idx))
            log.append(f"-- index {idx.name} recreated")


# ── Public API ────────────────────────────────────────────────────────────────


def migrate(conn: sqlite3.Connection, dry_run: bool = False) -> list[str]:
    """Reconcile the live database against the ORM model definitions.

    Args:
        conn: raw sqlite3 connection.
        dry_run: if True, compute ops but don't execute.

    Returns:
        List of SQL statements executed (or planned).
    """
    from prescribe.state.models import Base

    pragmas = {
        "journal_mode": "WAL",
        "busy_timeout": "30000",
        "foreign_keys": "ON",
    }
    for k, v in pragmas.items():
        current = _get_pragma(conn, k)
        if current.lower() != v.lower():
            conn.execute(f"PRAGMA {k}={v}")

    existing_tables = set(_list_user_tables(conn))
    all_ops: list[Op] = []
    for table in Base.metadata.sorted_tables:
        actual = _read_table(conn, table.name) if table.name in existing_tables else None
        all_ops.extend(_diff_table(table, actual))

    log: list[str] = []
    if not all_ops:
        return log

    if dry_run:
        for op in all_ops:
            if isinstance(op, RebuildTable):
                log.append(f"-- RebuildTable on '{op.table}': {op.reason}")
            elif isinstance(op, (CreateTable, AddColumn, CreateIndex)):
                log.append(op.sql)
            elif isinstance(op, DropIndex):
                log.append(f"DROP INDEX IF EXISTS {op.name}")
        return log

    conn.execute("BEGIN IMMEDIATE")
    try:
        for op in all_ops:
            _exec_op(conn, op, log)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return log
