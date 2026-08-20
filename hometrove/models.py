from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
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
    filename: Mapped[str] = mapped_column(Text, nullable=False, default="")
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
    # v1 trash: ``deleted_at`` is the soft-delete sentinel (epoch seconds).
    # ``None`` (NULL) means the asset is live; non-null means it has been
    # moved to trash and is hidden from default views. Permanent purge drops
    # the row; we never touch the on-disk file because M0 assumes scanned
    # media roots are read-only mounts.
    deleted_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # v1 favorite: 1 = favorited, 0 (or NULL) = not. The column is integer so
    # the existing tooling (sums / counts / indexable) handles it uniformly.
    # The default is 0 so newly ingested assets do not need a separate write.
    favorite: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # v2 content encryption (vault mode). When ``encrypted_path`` is set the
    # asset's on-disk payload lives inside the vault directory and is an
    # AES-256-GCM ciphertext; the column is NULL for plain assets that
    # resolve through the existing ``path`` field. ``encrypted_nonce`` is
    # the 12-byte nonce used by the payload header. ``origin_path`` is
    # populated during the optional vault import phase so the original
    # plaintext location can be retained for migration / verification.
    encrypted_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    encrypted_nonce: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    origin_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    plugin_results: Mapped[list["PluginResult"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("idx_assets_taken_at", "taken_at"),
        Index("idx_assets_media_type", "media_type"),
        Index("idx_assets_deleted_at", "deleted_at"),
        Index("idx_assets_favorite", "favorite"),
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
    """A user-named person in the library.

    ``name`` is the display label. ``info_json`` is a free JSON document for
    operator-managed attributes (height, age, notes, …) that lives
    independently of the tag system.

    A person is no longer directly tied to ``FaceEmbedding`` rows; instead
    it links to ``FaceCluster`` rows which themselves aggregate faces
    emitted by a single recognition plugin+model. This indirection keeps
    cross-plugin data isolated — switching recognition models never mixes
    vectors from different spaces.
    """

    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    info_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)

    clusters: Mapped[list["FaceCluster"]] = relationship(
        back_populates="person",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class FaceEmbeddingModel(Base):
    """Catalogue of registered face recognition models.

    The single occupant today is InsightFace ``buffalo_l`` (ArcFace R100,
    512-dim L2-normalized). New models are appended when a future pack is
    adopted; switching ``is_active`` controls which model new faces are
    matched against.
    """

    __tablename__ = "face_embedding_models"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)


class FaceCluster(Base):
    """A group of faces detected by one plugin+model that auto-cluster
    into the same identity.

    ``centroid_blob`` stores the cluster's running-mean vector as raw
    float32 bytes (``np.ndarray.tobytes()``) for cheap binary I/O.
    ``representative_face_id`` is set once ``face_count >= MIN_FACES``
    (default 3) so noise clusters don't surface in the UI.
    """

    __tablename__ = "face_clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("persons.id", ondelete="SET NULL"),
    )
    source_plugin_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_model_name: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    centroid_blob: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    radius: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    face_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    representative_face_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("face_embeddings.id", ondelete="SET NULL"),
    )
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)

    person: Mapped[Optional[Person]] = relationship(back_populates="clusters")
    faces: Mapped[list["FaceEmbedding"]] = relationship(
        back_populates="cluster",
        foreign_keys="FaceEmbedding.cluster_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("idx_face_clusters_person", "person_id"),
        Index(
            "idx_face_clusters_source_plugin_model",
            "source_plugin_id",
            "source_model_name",
        ),
    )


class FaceEmbedding(Base):
    """One detected face: its vector, source metadata, and cluster link.

    ``embedding_json`` holds the raw detection vector (list[float]) — the
    detector emits only vectors, never names; names come from ``persons``.
    ``person_id`` is kept for legacy callers that traverse Person→faces
    directly; new code goes Person→cluster→faces.
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

    # v2 fields
    cluster_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("face_clusters.id", ondelete="SET NULL"),
    )
    source_plugin_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_model_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    frame_index: Mapped[Optional[int]] = mapped_column(Integer)
    frame_t: Mapped[Optional[float]] = mapped_column(Float)

    person: Mapped[Optional[Person]] = relationship(
        foreign_keys=[person_id],
        overlaps="clusters",
    )
    cluster: Mapped[Optional["FaceCluster"]] = relationship(
        back_populates="faces",
        foreign_keys=[cluster_id],
        overlaps="person",
    )
    asset: Mapped[Asset] = relationship()

    __table_args__ = (
        Index("idx_face_embeddings_person", "person_id"),
        Index("idx_face_embeddings_asset", "asset_id"),
        Index("idx_face_embeddings_cluster", "cluster_id"),
        Index(
            "idx_face_embeddings_source_plugin_model",
            "source_plugin_id",
            "source_model_name",
        ),
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
    # v1 smart albums: 1 = rule-based album (membership evaluated on read),
    # 0 = manual album backed by album_assets.
    is_smart: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)

    items: Mapped[list["AlbumAsset"]] = relationship(
        back_populates="album",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AlbumAsset.position",
    )
    shares: Mapped[list["AlbumShare"]] = relationship(
        back_populates="album",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AlbumShare.created_at",
    )
    # v1 smart albums: one rule per smart album, deleted with the album.
    smart_rule: Mapped[Optional["SmartAlbumRule"]] = relationship(
        back_populates="album",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
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


class PluginPreset(Base):
    """A named set of plugins for upload presets.

    ``plugin_ids`` holds the list of plugin ids that should run on upload
    when this preset is selected. An empty list means "run all globally
    enabled plugins" (same as no preset). Built-in presets have ``is_builtin=True``
    and cannot be deleted.
    """

    __tablename__ = "plugin_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    is_builtin: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    plugin_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)


class AsrTranscript(Base):
    """A single speech-to-text segment produced by an ASR plugin.

    One row per detected utterance / cue point. ``t_start`` / ``t_end`` are
    seconds into the source media. ``text`` is the recognised utterance
    (trimmed, no timestamps). ``lang`` is the BCP-47 language tag the ASR
    reported (e.g. ``"zh"`` / ``"en"``); ``confidence`` is the model's
    segment-level score when available.

    The same source media may be re-transcribed with newer model versions;
    rows are versioned by ``plugin_version`` so v1 / v2 outputs coexist and
    the search/UI can pick one. The frontend's player joins ``t_start`` to
    jump to the matching second when the user clicks a transcript hit.
    """

    __tablename__ = "asr_transcripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False,
    )
    plugin_id: Mapped[str] = mapped_column(Text, nullable=False)
    plugin_version: Mapped[str] = mapped_column(Text, nullable=False, default="0.0.0")
    t_start: Mapped[float] = mapped_column(Float, nullable=False)
    t_end: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)

    __table_args__ = (
        Index("idx_asr_asset_plugin_version", "asset_id", "plugin_id", "plugin_version"),
        Index("idx_asr_asset_t_start", "asset_id", "t_start"),
    )


class AlbumShare(Base):
    """A public share link for an album.

    ``token`` is an opaque random token looked up on every public request.
    ``allow_original`` and ``allow_download`` control access to the original
    media file; ``expires_at`` optionally disables the link after a timestamp.
    Deleting an album cascades and revokes all its share links.
    """

    __tablename__ = "album_shares"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    album_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("albums.id", ondelete="CASCADE"), nullable=False,
    )
    token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    allow_original: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    allow_download: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)
    created_by: Mapped[str] = mapped_column(Text, nullable=False, default="local")

    album: Mapped["Album"] = relationship(back_populates="shares")

    __table_args__ = (
        Index("idx_album_shares_album", "album_id"),
        Index("idx_album_shares_token", "token"),
    )


class SmartAlbumRule(Base):
    """Stored rule expression for a smart album.

    ``album_id`` is both the primary key and a cascading foreign key to
    ``albums``. Deleting the album removes the rule. The rule is evaluated
    on every read by ``hometrove.smart_albums.eval_rule``.
    """

    __tablename__ = "smart_album_rules"

    album_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("albums.id", ondelete="CASCADE"),
        primary_key=True,
    )
    rule_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)

    album: Mapped["Album"] = relationship(back_populates="smart_rule")

    __table_args__ = (
        Index("idx_smart_album_rules_album", "album_id"),
    )


class VaultState(Base):
    """Singleton row that stores the encrypted vault master key.

    ``id`` is constrained to ``1`` by a CHECK constraint on the table so
    the application can rely on ``session.get(VaultState, 1)`` returning
    the only row. ``kdf_salt`` and ``kdf_params_json`` capture the
    Argon2id parameters used to derive the KEK from the user's master
    password; ``wrapped_master_key`` is the AES-Key-Wrap output of the
    raw 96-byte master key under that KEK, so the table never holds
    plaintext key material.
    """

    __tablename__ = "vault_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kdf_salt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    kdf_params_json: Mapped[str] = mapped_column(Text, nullable=False)
    wrapped_master_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)


class AppSetting(Base):
    """Generic key/value store for app-wide, user-toggled settings.

    Persisted in the DB (not env vars) so the **Settings** UI can flip
    flags without restarting the API. The first occupant is
    ``encrypt_new_uploads`` — the global encryption toggle for new
    uploads. Values are stored as text; consumers parse them
    (``"true"`` / ``"false"`` for booleans).
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)
