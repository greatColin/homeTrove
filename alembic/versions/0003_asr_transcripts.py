"""0003 asr_transcripts for M1-10 speech-to-text storage."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_asr_transcripts"
down_revision = "0002_persons_faces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asr_transcripts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("asset_id", sa.Integer, nullable=False),
        sa.Column("plugin_id", sa.Text, nullable=False),
        sa.Column("plugin_version", sa.Text, nullable=False, server_default="0.0.0"),
        sa.Column("t_start", sa.Float, nullable=False),
        sa.Column("t_end", sa.Float, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("lang", sa.Text),
        sa.Column("confidence", sa.Float),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["assets.id"], ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_asr_asset_plugin_version",
        "asr_transcripts",
        ["asset_id", "plugin_id", "plugin_version"],
    )
    op.create_index(
        "idx_asr_asset_t_start",
        "asr_transcripts",
        ["asset_id", "t_start"],
    )


def downgrade() -> None:
    op.drop_index("idx_asr_asset_t_start", table_name="asr_transcripts")
    op.drop_index("idx_asr_asset_plugin_version", table_name="asr_transcripts")
    op.drop_table("asr_transcripts")