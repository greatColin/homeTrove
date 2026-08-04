"""M0 baseline: assets, plugin_results, jobs, plugin_config."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001_m0_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("path", sa.Text, nullable=False, unique=True),
        sa.Column("media_root", sa.Text, nullable=False),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("media_type", sa.Text, nullable=False),
        sa.Column("size_bytes", sa.BigInteger),
        sa.Column("mtime", sa.Integer),
        sa.Column("taken_at", sa.Integer),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("duration_sec", sa.Float),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.Integer, nullable=False),
    )
    op.create_index("idx_assets_taken_at", "assets", ["taken_at"])
    op.create_index("idx_assets_media_type", "assets", ["media_type"])

    op.create_table(
        "plugin_results",
        sa.Column("asset_id", sa.Integer, nullable=False),
        sa.Column("plugin_id", sa.Text, nullable=False),
        sa.Column("plugin_version", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("result_json", sa.Text, nullable=False),
        sa.Column("elapsed_ms", sa.Integer),
        sa.Column("finished_at", sa.Integer),
        sa.PrimaryKeyConstraint(
            "asset_id", "plugin_id", "plugin_version",
            name="pk_plugin_results",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["assets.id"], ondelete="CASCADE",
        ),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("asset_id", sa.Integer, nullable=False),
        sa.Column("plugin_id", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("est_cost", sa.Float, nullable=False, server_default="0"),
        sa.Column("actual_cost", sa.Float),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column("enqueued_at", sa.Integer, nullable=False),
        sa.Column("started_at", sa.Integer),
        sa.Column("finished_at", sa.Integer),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["assets.id"], ondelete="CASCADE",
        ),
    )
    op.create_index("idx_jobs_state", "jobs", ["state"])

    op.create_table(
        "plugin_config",
        sa.Column("plugin_id", sa.Text, primary_key=True),
        sa.Column("enabled", sa.Integer, nullable=False, server_default="1"),
        sa.Column("params_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("calib", sa.Float, nullable=False, server_default="1.0"),
    )


def downgrade() -> None:
    op.drop_table("plugin_config")
    op.drop_index("idx_jobs_state", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("plugin_results")
    op.drop_index("idx_assets_media_type", table_name="assets")
    op.drop_index("idx_assets_taken_at", table_name="assets")
    op.drop_table("assets")
