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


class Person(Base):
    """A person the face matcher has grouped faces under.

    ``name`` is a display label — auto-created entries get an opaque
    ``未命名-<rand>`` label until the user edits it. ``info_json`` is a free
    JSON document for operator-managed attributes (height, age, notes, …) that
    lives independently of the tag system.
    """

    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    info_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)

    faces: Mapped[list["FaceEmbedding"]] = relationship(
        back_populates="person",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class FaceEmbedding(Base):
    """One detected face: its vector and the person it was matched to.

    ``embedding_json`` holds the raw detection vector (list[float]) — the
    detector emits only vectors, never names; names come from ``persons``.
    """

    __tablename__ = "face_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("persons.id", ondelete="SET NULL"),
    )
    asset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False,
    )
    embedding_json: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    box_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)

    person: Mapped[Optional[Person]] = relationship(back_populates="faces")
    asset: Mapped[Asset] = relationship()

    __table_args__ = (
        Index("idx_face_embeddings_person", "person_id"),
        Index("idx_face_embeddings_asset", "asset_id"),
    )


class Embedding(Base):
    """A semantic vector for an asset, produced by an embedding plugin.

    ``scope`` distinguishes what the vector describes:

    * ``image``  — the whole photo / the full video's cover frame
    * ``scene``  — one video scene (``t_start``/``t_end`` in seconds)
    * ``caption``— a natural-language description (M1-5 VLM output, later)

    The vector itself lives both in ``embedding_json`` (source of truth) and
    in the ``embedding_vec`` sqlite-vec virtual table (search index), whose
    ``rowid`` mirrors this table's primary key.
    """

    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False,
    )
    plugin_id: Mapped[str] = mapped_column(Text, nullable=False)
    plugin_version: Mapped[str] = mapped_column(Text, nullable=False, default="0.0.0")
    scope: Mapped[str] = mapped_column(Text, nullable=False, default="image")
    t_start: Mapped[Optional[float]] = mapped_column(Float)
    t_end: Mapped[Optional[float]] = mapped_column(Float)
    embedding_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)

    asset: Mapped[Asset] = relationship()

    __table_args__ = (
        Index("idx_embeddings_asset_scope", "asset_id", "scope"),
        Index("idx_embeddings_asset_plugin", "asset_id", "plugin_id"),
    )


class Album(Base):
    """A manually curated collection of assets.

    ``cover_asset_id`` optionally pins a representative photo for the album
    list grid; when null the frontend falls back to the first asset by
    position. Assets are ordered by ``AlbumAsset.position``.
    """

    __tablename__ = "albums"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cover_asset_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("assets.id", ondelete="SET NULL")
    )
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)

    items: Mapped[list["AlbumAsset"]] = relationship(
        back_populates="album",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AlbumAsset.position",
    )


class AlbumAsset(Base):
    __tablename__ = "album_assets"

    album_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("albums.id", ondelete="CASCADE"), primary_key=True,
    )
    asset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)

    album: Mapped[Album] = relationship(back_populates="items")
    asset: Mapped[Asset] = relationship()


class PluginConfig(Base):
    __tablename__ = "plugin_config"

    plugin_id: Mapped[str] = mapped_column(Text, primary_key=True)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    calib: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
