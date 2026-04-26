"""Diff desired vs actual schema, emit migration operations."""

from dataclasses import dataclass

from prescribe.state.schema import Column, Index, Table


@dataclass(frozen=True)
class CreateTable:
    table: Table


@dataclass(frozen=True)
class AddColumn:
    table: str
    column_ddl: str
    column_name: str


@dataclass(frozen=True)
class CreateIndex:
    table: str
    index: Index


@dataclass(frozen=True)
class DropIndex:
    name: str


@dataclass(frozen=True)
class RebuildTable:
    """Drop-column or non-additive index change. Copy preserved cols across."""

    desired: Table
    preserved_columns: tuple[str, ...]


@dataclass(frozen=True)
class ArchiveAndRecreate:
    """PK or type change: archive old data, create fresh empty table."""

    desired: Table
    reason: str


Op = CreateTable | AddColumn | CreateIndex | DropIndex | RebuildTable | ArchiveAndRecreate


def _columns_compatible(desired_col: Column, actual_col: Column) -> tuple[bool, str]:
    """Check if a shared column is compatible. Returns (ok, reason_if_not)."""
    if desired_col.type != actual_col.type:
        return False, f"type {actual_col.type} -> {desired_col.type}"
    if desired_col.pk != actual_col.pk:
        return False, "primary key membership changed"
    if desired_col.references != actual_col.references:
        return False, "foreign key changed"
    return True, ""


def diff_table(desired: Table, actual: Table | None) -> list[Op]:
    if actual is None:
        ops: list[Op] = [CreateTable(desired)]
        ops.extend(CreateIndex(desired.name, i) for i in desired.indexes)
        return ops

    desired_cols = desired.column_map()
    actual_cols = actual.column_map()
    d_names = set(desired_cols)
    a_names = set(actual_cols)

    # PK or type change on shared columns -> archive + recreate fresh
    for name in d_names & a_names:
        ok, reason = _columns_compatible(desired_cols[name], actual_cols[name])
        if not ok:
            return [ArchiveAndRecreate(desired, f"column '{name}': {reason}")]

    # PK column set change (added/removed PK columns) -> archive + recreate
    if set(desired.pk_columns()) != set(actual.pk_columns()):
        return [ArchiveAndRecreate(desired, "primary key composition changed")]

    added = d_names - a_names
    removed = a_names - d_names

    # Determine if we need a table rebuild (drops, or index touches dropped cols)
    needs_rebuild = bool(removed)

    # Index diff
    desired_idx = desired.index_map()
    actual_idx = actual.index_map()

    # Indexes are compared by (name, columns-tuple, unique). A name collision
    # with different columns means drop+recreate.
    result: list[Op] = []

    if needs_rebuild:
        preserved = tuple(c.name for c in desired.columns if c.name in a_names)
        result.append(RebuildTable(desired, preserved))
        # Rebuild recreates table fresh; all desired indexes will be made there.
        return result

    # Pure superset: ADD COLUMN for each new column.
    # SQLite restriction: ADD COLUMN cannot add PRIMARY KEY or UNIQUE; if the
    # added col is PK we'd need a rebuild. Caller-defined PKs on existing
    # tables shouldn't be added post-hoc, so flag that.
    for name in added:
        col = desired_cols[name]
        if col.pk:
            return [ArchiveAndRecreate(desired, f"cannot ADD COLUMN '{name}' as PRIMARY KEY")]
        result.append(AddColumn(desired.name, col.ddl(), name))

    # Index changes (after columns settled)
    for iname, idx in actual_idx.items():
        if iname not in desired_idx:
            result.append(DropIndex(iname))
        elif desired_idx[iname].columns != idx.columns or desired_idx[iname].unique != idx.unique:
            result.append(DropIndex(iname))
            result.append(CreateIndex(desired.name, desired_idx[iname]))
    for iname, idx in desired_idx.items():
        if iname not in actual_idx:
            result.append(CreateIndex(desired.name, idx))

    return result
