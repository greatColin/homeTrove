"""0002 persons and face_embeddings for face grouping."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_persons_faces"
down_revision = "0001_m0_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "persons",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("info_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.Integer, nullable=False),
    )

    op.create_table(
        "face_embeddings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("asset_id", sa.Integer, nullable=False),
        sa.Column("embedding_json", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float),
        sa.Column("box_json", sa.Text),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.ForeignKeyConstraint(
            ["person_id"], ["persons.id"], ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["assets.id"], ondelete="CASCADE",
        ),
    )
    op.create_index("idx_face_embeddings_person", "face_embeddings", ["person_id"])
    op.create_index("idx_face_embeddings_asset", "face_embeddings", ["asset_id"])


def downgrade() -> None:
    op.drop_index("idx_face_embeddings_asset", table_name="face_embeddings")
    op.drop_index("idx_face_embeddings_person", table_name="face_embeddings")
    op.drop_table("face_embeddings")
    op.drop_table("persons")
