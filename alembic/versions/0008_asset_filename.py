"""0008 add filename column to assets for searching and display."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_asset_filename"
down_revision = "0007_smart_albums"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("filename", sa.Text, nullable=True))
    op.create_index("idx_assets_filename", "assets", ["filename"])

    # Backfill filename from the stored path (root\0rel_path or plain path).
    op.execute(
        """
        UPDATE assets
        SET filename = CASE
            WHEN instr(path, char(0)) > 0 THEN substr(path, instr(path, char(0)) + 1)
            ELSE path
        END
        """
    )


def downgrade() -> None:
    op.drop_index("idx_assets_filename", table_name="assets")
    op.drop_column("assets", "filename")
