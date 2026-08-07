"""0004 asset trash for v1 soft delete (30 day retention default)."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_asset_trash"
down_revision = "0003_asr_transcripts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("deleted_at", sa.Integer, nullable=True),
    )
    op.create_index("idx_assets_deleted_at", "assets", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("idx_assets_deleted_at", table_name="assets")
    op.drop_column("assets", "deleted_at")
