"""SQLAlchemy ORM models — canonical schema definition.

Models here are the single source of truth for the database schema.
Auto-migration reads from ``Base.metadata`` on startup.
"""

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, LargeBinary, Text
from sqlalchemy.orm import DeclarativeBase

from prescribe.state.snowflake import snowflake_id


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    id = Column(Integer, primary_key=True, default=snowflake_id)
    started_at = Column(Text, nullable=False)
    spec_hash = Column(LargeBinary)
    tool_version = Column(Text)
    host = Column(Text)
    platform = Column(Text)


class FileSnapshot(Base):
    __tablename__ = "file_snapshots"

    id = Column(Integer, primary_key=True, default=snowflake_id)
    run_id = Column(Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    path = Column(Text, nullable=False)
    captured_at = Column(Text, nullable=False)
    hash_algo = Column(Text, nullable=False)
    content_hash = Column(LargeBinary, nullable=False)
    size = Column(Integer, nullable=False)
    mtime_ns = Column(Integer)
    format = Column(Text)
    spec_hash = Column(LargeBinary)

    __table_args__ = (Index("idx_file_snapshots_path_id", "path", "id"),)


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, default=snowflake_id)
    run_id = Column(Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(Text, nullable=False)
    event_type = Column(Text, nullable=False)
    path = Column(Text)
    changed = Column(Boolean, nullable=False)
    summary = Column(Text)
    details = Column(Text)

    __table_args__ = (Index("idx_events_run_id_id", "run_id", "id"),)


class ChangeBatch(Base):
    __tablename__ = "change_batches"

    id = Column(Integer, primary_key=True, default=snowflake_id)
    run_id = Column(Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    path = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)
    format = Column(Text)
    original_exists = Column(Boolean, nullable=False)
    operations_json = Column(Text, nullable=False)

    __table_args__ = (Index("idx_change_batches_path_id", "path", "id"),)


class FileCheckpoint(Base):
    __tablename__ = "file_checkpoints"

    id = Column(Integer, primary_key=True, default=snowflake_id)
    run_id = Column(Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    path = Column(Text, nullable=False)
    captured_at = Column(Text, nullable=False)
    format = Column(Text)
    content_text = Column(Text, nullable=False)
    original_exists = Column(Boolean, nullable=False)

    __table_args__ = (Index("idx_file_checkpoints_path_id", "path", "id"),)


class FileBaseline(Base):
    __tablename__ = "file_baselines"

    id = Column(Integer, primary_key=True, default=snowflake_id)
    run_id = Column(Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    path = Column(Text, nullable=False)
    captured_at = Column(Text, nullable=False)
    format = Column(Text)
    content_text = Column(Text, nullable=False)
    original_exists = Column(Boolean, nullable=False)

    __table_args__ = (Index("idx_file_baselines_path_id", "path", "id"),)
