from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hometrove.db import Base


def _now() -> int:
    return int(datetime.now().timestamp())


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    media_root: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    mtime: Mapped[Optional[int]] = mapped_column(Integer)
    taken_at: Mapped[Optional[int]] = mapped_column(Integer)
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    duration_sec: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)

    plugin_results: Mapped[list["PluginResult"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("idx_assets_taken_at", "taken_at"),
        Index("idx_assets_media_type", "media_type"),
    )


class PluginResult(Base):
    __tablename__ = "plugin_results"

    asset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True,
    )
    plugin_id: Mapped[str] = mapped_column(Text, primary_key=True)
    plugin_version: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    elapsed_ms: Mapped[Optional[int]] = mapped_column(Integer)
    finished_at: Mapped[Optional[int]] = mapped_column(Integer)

    asset: Mapped[Asset] = relationship(back_populates="plugin_results")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False,
    )
    plugin_id: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    est_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    actual_cost: Mapped[Optional[float]] = mapped_column(Float)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text)
    enqueued_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)
    started_at: Mapped[Optional[int]] = mapped_column(Integer)
    finished_at: Mapped[Optional[int]] = mapped_column(Integer)

    __table_args__ = (Index("idx_jobs_state", "state"),)


class PluginConfig(Base):
    __tablename__ = "plugin_config"

    plugin_id: Mapped[str] = mapped_column(Text, primary_key=True)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    calib: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
