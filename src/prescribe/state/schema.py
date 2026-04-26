"""Declarative schema definitions."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ForeignKey:
    table: str
    column: str = "id"
    on_delete: str = "CASCADE"


@dataclass(frozen=True)
class Column:
    name: str
    type: str  # "TEXT", "INTEGER", "REAL", "BLOB", "NUMERIC"
    notnull: bool = False
    default: str | None = None  # raw SQL literal, e.g. "0" or "'pending'"
    pk: bool = False  # part of primary key
    autoincrement: bool = False  # only valid for INTEGER PK
    references: ForeignKey | None = None

    def ddl(self) -> str:
        parts = [self.name, self.type]
        if self.notnull:
            parts.append("NOT NULL")
        if self.references is not None:
            fk = self.references
            parts.append(f"REFERENCES {fk.table}({fk.column}) ON DELETE {fk.on_delete}")
        if self.default is not None:
            parts.append(f"DEFAULT {self.default}")
        return " ".join(parts)


@dataclass(frozen=True)
class Index:
    name: str
    columns: tuple[str, ...]  # ordered; (a,b) != (b,a)
    unique: bool = False
    descending: bool = False

    def ddl(self, table: str) -> str:
        u = "UNIQUE " if self.unique else ""
        cols = ", ".join(self.columns)
        suffix = " DESC" if self.descending else ""
        return f"CREATE {u}INDEX IF NOT EXISTS {self.name} ON {table}({cols}{suffix})"


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    indexes: tuple[Index, ...] = ()

    def column_map(self) -> dict[str, Column]:
        return {c.name: c for c in self.columns}

    def index_map(self) -> dict[str, Index]:
        return {i.name: i for i in self.indexes}

    def pk_columns(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.pk)

    def ddl(self) -> str:
        col_defs = [c.ddl() for c in self.columns]
        pk = self.pk_columns()
        if len(pk) > 1:
            col_defs.append(f"PRIMARY KEY ({', '.join(pk)})")
        elif len(pk) == 1:
            # inline single-column PK
            idx = next(i for i, c in enumerate(self.columns) if c.pk)
            pk_col = self.columns[idx]
            ai = " AUTOINCREMENT" if pk_col.autoincrement else ""
            col_defs[idx] = col_defs[idx] + f" PRIMARY KEY{ai}"
        body = ",\n  ".join(col_defs)
        return f"CREATE TABLE IF NOT EXISTS {self.name} (\n  {body}\n)"


@dataclass(frozen=True)
class Schema:
    tables: tuple[Table, ...]
    pragmas: dict[str, str] = field(
        default_factory=lambda: {
            "journal_mode": "WAL",
            "synchronous": "NORMAL",
            "busy_timeout": "30000",
        }
    )

    def table_map(self) -> dict[str, Table]:
        return {t.name: t for t in self.tables}


FK_RUNS = ForeignKey(table="runs")

CURRENT_SCHEMA = Schema(
    tables=(
        Table(
            name="runs",
            columns=(
                Column(name="id", type="INTEGER", pk=True, autoincrement=True),
                Column(name="started_at", type="TEXT", notnull=True),
                Column(name="spec_hash", type="BLOB"),
                Column(name="tool_version", type="TEXT"),
                Column(name="host", type="TEXT"),
                Column(name="platform", type="TEXT"),
            ),
        ),
        Table(
            name="file_snapshots",
            columns=(
                Column(name="id", type="INTEGER", pk=True, autoincrement=True),
                Column(name="run_id", type="INTEGER", notnull=True, references=FK_RUNS),
                Column(name="path", type="TEXT", notnull=True),
                Column(name="captured_at", type="TEXT", notnull=True),
                Column(name="hash_algo", type="TEXT", notnull=True),
                Column(name="content_hash", type="BLOB", notnull=True),
                Column(name="size", type="INTEGER", notnull=True),
                Column(name="mtime_ns", type="INTEGER"),
                Column(name="format", type="TEXT"),
                Column(name="spec_hash", type="BLOB"),
            ),
            indexes=(Index(name="idx_file_snapshots_path_id", columns=("path", "id"), descending=True),),
        ),
        Table(
            name="events",
            columns=(
                Column(name="id", type="INTEGER", pk=True, autoincrement=True),
                Column(name="run_id", type="INTEGER", notnull=True, references=FK_RUNS),
                Column(name="created_at", type="TEXT", notnull=True),
                Column(name="event_type", type="TEXT", notnull=True),
                Column(name="path", type="TEXT"),
                Column(name="changed", type="INTEGER", notnull=True),
                Column(name="summary", type="TEXT"),
                Column(name="details", type="TEXT"),
            ),
            indexes=(Index(name="idx_events_run_id_id", columns=("run_id", "id"), descending=True),),
        ),
        Table(
            name="change_batches",
            columns=(
                Column(name="id", type="INTEGER", pk=True, autoincrement=True),
                Column(name="run_id", type="INTEGER", notnull=True, references=FK_RUNS),
                Column(name="path", type="TEXT", notnull=True),
                Column(name="created_at", type="TEXT", notnull=True),
                Column(name="format", type="TEXT"),
                Column(name="original_exists", type="INTEGER", notnull=True),
                Column(name="operations_json", type="TEXT", notnull=True),
            ),
            indexes=(Index(name="idx_change_batches_path_id", columns=("path", "id"), descending=True),),
        ),
        Table(
            name="file_checkpoints",
            columns=(
                Column(name="id", type="INTEGER", pk=True, autoincrement=True),
                Column(name="run_id", type="INTEGER", notnull=True, references=FK_RUNS),
                Column(name="path", type="TEXT", notnull=True),
                Column(name="captured_at", type="TEXT", notnull=True),
                Column(name="format", type="TEXT"),
                Column(name="content_text", type="TEXT", notnull=True),
                Column(name="original_exists", type="INTEGER", notnull=True),
            ),
            indexes=(Index(name="idx_file_checkpoints_path_id", columns=("path", "id"), descending=True),),
        ),
        Table(
            name="file_baselines",
            columns=(
                Column(name="id", type="INTEGER", pk=True, autoincrement=True),
                Column(name="run_id", type="INTEGER", notnull=True, references=FK_RUNS),
                Column(name="path", type="TEXT", notnull=True),
                Column(name="captured_at", type="TEXT", notnull=True),
                Column(name="format", type="TEXT"),
                Column(name="content_text", type="TEXT", notnull=True),
                Column(name="original_exists", type="INTEGER", notnull=True),
            ),
            indexes=(Index(name="idx_file_baselines_path_id", columns=("path", "id")),),
        ),
    ),
    pragmas={
        "journal_mode": "WAL",
        "synchronous": "NORMAL",
        "busy_timeout": "30000",
    },
)
