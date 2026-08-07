"""0006 shared album links with permissions and expiration."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_shared_albums"
down_revision = "0005_favorites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "album_shares",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "album_id",
            sa.Integer,
            sa.ForeignKey("albums.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token", sa.Text, nullable=False, unique=True),
        sa.Column("allow_original", sa.Integer, nullable=False, server_default="0"),
        sa.Column("allow_download", sa.Integer, nullable=False, server_default="0"),
        sa.Column("expires_at", sa.Integer, nullable=True),
        sa.Column("created_at", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_by", sa.Text, nullable=False, server_default="local"),
    )
    op.create_index("idx_album_shares_album", "album_shares", ["album_id"])
    op.create_index("idx_album_shares_token", "album_shares", ["token"])


def downgrade() -> None:
    op.drop_index("idx_album_shares_token", table_name="album_shares")
    op.drop_index("idx_album_shares_album", table_name="album_shares")
    op.drop_table("album_shares")
