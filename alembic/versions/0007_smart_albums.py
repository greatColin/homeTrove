"""0007 smart albums with JSON rule expressions."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_smart_albums"
down_revision = "0006_shared_albums"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "albums",
        sa.Column("is_smart", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "smart_album_rules",
        sa.Column(
            "album_id",
            sa.Integer,
            sa.ForeignKey("albums.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("rule_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("smart_album_rules")
    op.drop_column("albums", "is_smart")
