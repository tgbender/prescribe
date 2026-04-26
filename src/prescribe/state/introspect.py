"""Read live SQLite state into Schema dataclass shape."""

import sqlite3

from prescribe.state.schema import Column, ForeignKey, Index, Table


def get_pragma(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(f"PRAGMA {name}").fetchone()
    return str(row[0]) if row else ""


def list_user_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return [r[0] for r in rows]


def read_table(conn: sqlite3.Connection, name: str) -> Table | None:
    """Return Table reflecting actual SQLite state, or None if missing."""
    info = conn.execute(f"PRAGMA table_info({name})").fetchall()
    if not info:
        return None

    # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
    columns = tuple(
        Column(
            name=row[1],
            type=row[2].upper() if row[2] else "",
            notnull=bool(row[3]),
            default=row[4],
            pk=bool(row[5]),
        )
        for row in info
    )

    # Indexes: skip auto-indexes (PK, UNIQUE constraints create those)
    idx_rows = conn.execute(f"PRAGMA index_list({name})").fetchall()
    # (seq, name, unique, origin, partial)
    indexes = []
    for irow in idx_rows:
        iname, unique, origin = irow[1], bool(irow[2]), irow[3]
        if origin != "c":  # 'c' = created via CREATE INDEX (not PK/UNIQUE auto)
            continue
        col_rows = conn.execute(f"PRAGMA index_info({iname})").fetchall()
        # (seqno, cid, name) - sort by seqno to preserve declared order
        col_rows.sort(key=lambda r: r[0])
        cols = tuple(r[2] for r in col_rows)
        indexes.append(Index(name=iname, columns=cols, unique=unique))

    # Foreign keys
    fk_rows = conn.execute(f"PRAGMA foreign_key_list({name})").fetchall()
    # (id, seq, table, from, to, on_update, on_delete, match)
    fk_map: dict[str, ForeignKey] = {}
    for frow in fk_rows:
        col_name = frow[3]  # 'from' column
        fk_map[col_name] = ForeignKey(table=frow[2], column=frow[4], on_delete=frow[6])

    # Attach FK references to columns
    columns_with_fk = tuple(
        Column(
            name=c.name,
            type=c.type,
            notnull=c.notnull,
            default=c.default,
            pk=c.pk,
            references=fk_map.get(c.name),
        )
        for c in columns
    )

    # Detect AUTOINCREMENT from sqlite_master DDL
    master_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    autoinc_cols: set[str] = set()
    if master_row and master_row[0]:
        import re

        for m in re.finditer(r"(\w+)\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", master_row[0], re.IGNORECASE):
            autoinc_cols.add(m.group(1).lower())

    columns_final = tuple(
        Column(
            name=c.name,
            type=c.type,
            notnull=c.notnull,
            default=c.default,
            pk=c.pk,
            autoincrement=c.name.lower() in autoinc_cols,
            references=c.references,
        )
        for c in columns_with_fk
    )

    return Table(name=name, columns=columns_final, indexes=tuple(indexes))
