from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from prescribe.state.migrate import migrate

_PER_CONN_PRAGMAS = (
    "PRAGMA busy_timeout=30000",
    "PRAGMA foreign_keys=ON",
    "PRAGMA synchronous=NORMAL",
)


def _apply_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cursor = dbapi_conn.cursor()
    try:
        for statement in _PER_CONN_PRAGMAS:
            cursor.execute(statement)
    finally:
        cursor.close()


def engine_for_connection_factory(
    *,
    path: Path,
    connection_factory: Callable[[Path], sqlite3.Connection],
) -> Engine:
    def creator() -> sqlite3.Connection:
        return connection_factory(path)

    engine = create_engine("sqlite://", creator=creator, poolclass=NullPool)
    event.listen(engine, "connect", _apply_pragmas)
    return engine


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


def initialize_engine(engine: Engine) -> None:
    with engine.connect() as connection:
        migrate(cast(sqlite3.Connection, connection.connection.driver_connection))
