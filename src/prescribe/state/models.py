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
    ended_at = Column(Text)
    status = Column(Text)
    command = Column(Text)
    cwd = Column(Text)


class SpecRun(Base):
    __tablename__ = "spec_runs"

    id = Column(Integer, primary_key=True, default=snowflake_id)
    run_id = Column(Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    spec_path = Column(Text, nullable=False)
    spec_hash = Column(LargeBinary)
    order_index = Column(Integer, nullable=False)
    valid = Column(Boolean, nullable=False)

    __table_args__ = (Index("idx_spec_runs_run_id_order", "run_id", "order_index"),)


class TargetRun(Base):
    __tablename__ = "target_runs"

    id = Column(Integer, primary_key=True, default=snowflake_id)
    run_id = Column(Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    spec_run_id = Column(Integer, ForeignKey("spec_runs.id", ondelete="CASCADE"))
    target_type = Column(Text, nullable=False)
    target_id = Column(Text)
    path_or_name = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    skip_reason = Column(Text)
    changed = Column(Boolean, nullable=False)

    __table_args__ = (Index("idx_target_runs_run_id_type", "run_id", "target_type"),)


class RunLock(Base):
    __tablename__ = "run_locks"

    name = Column(Text, primary_key=True)
    owner = Column(Text, nullable=False)
    run_id = Column(Integer, ForeignKey("runs.id", ondelete="SET NULL"))
    acquired_at = Column(Text, nullable=False)
    expires_at = Column(Text, nullable=False)


class ManagedClaim(Base):
    __tablename__ = "managed_claims"

    id = Column(Integer, primary_key=True, default=snowflake_id)
    target_type = Column(Text, nullable=False)
    subject = Column(Text, nullable=False)
    address = Column(Text, nullable=False)
    owner_id = Column(Text, nullable=False)
    spec_path = Column(Text)
    target_id = Column(Text)
    created_at = Column(Text, nullable=False)
    last_seen_at = Column(Text, nullable=False)

    __table_args__ = (
        Index("uq_managed_claims_target_subject_address", "target_type", "subject", "address", unique=True),
        Index("idx_managed_claims_owner", "owner_id"),
    )


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


class AssetBackup(Base):
    __tablename__ = "asset_backups"

    id = Column(Integer, primary_key=True, default=snowflake_id)
    run_id = Column(Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    target_dest = Column(Text, nullable=False)
    original_path = Column(Text, nullable=False)
    backup_path = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)
    hash_algo = Column(Text, nullable=False)
    content_hash = Column(LargeBinary, nullable=False)
    size = Column(Integer, nullable=False)
    mtime_ns = Column(Integer)
    file_type = Column(Text, nullable=False)
    restored_at = Column(Text)

    __table_args__ = (
        Index("idx_asset_backups_original_path_id", "original_path", "id"),
        Index("idx_asset_backups_target_dest_id", "target_dest", "id"),
    )


class RecoveryBackup(Base):
    __tablename__ = "recovery_backups"

    id = Column(Integer, primary_key=True, default=snowflake_id)
    run_id = Column(Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    target_path = Column(Text, nullable=False)
    target_kind = Column(Text, nullable=False)
    operation = Column(Text, nullable=False)
    backup_path = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)
    hash_algo = Column(Text, nullable=False)
    content_hash = Column(LargeBinary, nullable=False)
    size = Column(Integer, nullable=False)
    mtime_ns = Column(Integer)
    content_text = Column(Text)

    __table_args__ = (
        Index("idx_recovery_backups_target_path_id", "target_path", "id"),
        Index("idx_recovery_backups_run_id", "run_id"),
    )
