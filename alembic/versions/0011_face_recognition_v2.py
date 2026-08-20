"""0011 face recognition v2 — clusters + per-face source metadata.

Adds two new tables (``face_clusters``, ``face_embedding_models``) and a
the new column on ``face_embeddings`` (``cluster_id`` and source metadata).

Why ``cluster_id`` lives next to ``person_id`` on ``face_embeddings``:

* New auto-cluster pipeline writes a cluster row per match and links faces
  via ``face_embeddings.cluster_id``.
* ``person_id`` is kept for backward-compat with the v1 matcher and will be
  dropped once all callers go through cluster → person indirection.

``centroid_blob`` is the cluster's running-mean embedding stored as
``np.ndarray(dtype=float32, shape=(512,)).tobytes()`` for cheap binary I/O
versus JSON parsing on every match. The constant ``EMBEDDING_DIM = 512``
matches InsightFace buffalo_l (ArcFace R100).
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_face_recognition_v2"
down_revision = "0010_app_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ----- face_embeddings: add cluster + source metadata -----
    with op.batch_alter_table("face_embeddings") as batch:
        batch.add_column(sa.Column("cluster_id", sa.Integer, nullable=True))
        batch.add_column(sa.Column("source_plugin_id", sa.Text, nullable=False, server_default=""))
        batch.add_column(sa.Column("source_model_name", sa.Text, nullable=False, server_default=""))
        batch.add_column(sa.Column("frame_index", sa.Integer, nullable=True))
        batch.add_column(sa.Column("frame_t", sa.Float, nullable=True))
        batch.create_index("idx_face_embeddings_cluster", ["cluster_id"])
        batch.create_index(
            "idx_face_embeddings_source_plugin_model",
            ["source_plugin_id", "source_model_name"],
        )

    # ----- face_clusters: per-plugin+model grouping of faces -----
    op.create_table(
        "face_clusters",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "person_id",
            sa.Integer,
            sa.ForeignKey("persons.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_plugin_id", sa.Text, nullable=False),
        sa.Column("source_model_name", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("centroid_blob", sa.LargeBinary, nullable=True),
        sa.Column("radius", sa.Float, nullable=False, server_default="0"),
        sa.Column("face_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "representative_face_id",
            sa.Integer,
            nullable=True,
        ),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.Integer, nullable=False),
    )
    op.create_index("idx_face_clusters_person", "face_clusters", ["person_id"])
    op.create_index(
        "idx_face_clusters_source_plugin_model",
        "face_clusters",
        ["source_plugin_id", "source_model_name"],
    )
    # Face-to-cluster FK and cluster.representative_face FK are added after
    # both tables exist (SQLite FK cycles are not allowed in CREATE TABLE).
    with op.batch_alter_table("face_embeddings") as batch:
        batch.create_foreign_key(
            "fk_face_embeddings_cluster_id",
            "face_clusters",
            ["cluster_id"], ["id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table("face_clusters") as batch:
        batch.create_foreign_key(
            "fk_face_clusters_representative_face_id",
            "face_embeddings",
            ["representative_face_id"], ["id"],
            ondelete="SET NULL",
        )

    # ----- face_embedding_models: catalogue of registered recognition models -----
    op.create_table(
        "face_embedding_models",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("dim", sa.Integer, nullable=False),
        sa.Column("created_at", sa.Integer, nullable=False),
    )
    op.execute(
        "INSERT INTO face_embedding_models (name, dim, created_at) "
        "VALUES ('buffalo_l', 512, CAST(strftime('%s','now') AS INTEGER))"
    )


def downgrade() -> None:
    op.drop_table("face_embedding_models")
    op.drop_table("face_clusters")
    with op.batch_alter_table("face_embeddings") as batch:
        batch.drop_index("idx_face_embeddings_source_plugin_model")
        batch.drop_index("idx_face_embeddings_cluster")
        batch.drop_column("frame_t")
        batch.drop_column("frame_index")
        batch.drop_column("source_model_name")
        batch.drop_column("source_plugin_id")
        batch.drop_column("cluster_id")