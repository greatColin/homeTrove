"""0005 favorite flag for v1 bulk / favorites."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_favorites"
down_revision = "0004_asset_trash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``favorite`` is integer (0/1) rather than boolean so the existing
    # indexing / COUNT / SUM helpers all work uniformly. Default 0 keeps
    # existing rows consistent without a server-side default update.
    op.add_column(
        "assets",
        sa.Column("favorite", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("idx_assets_favorite", "assets", ["favorite"])


def downgrade() -> None:
    op.drop_index("idx_assets_favorite", table_name="assets")
    op.drop_column("assets", "favorite")
