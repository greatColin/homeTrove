"""0010 app_settings key/value store.

Adds a single ``app_settings`` table keyed by ``key`` (TEXT) with a TEXT
``value``. Today it only holds ``encrypt_new_uploads`` (a boolean flag
the user flips in **Settings** to globally force new uploads to be
encrypted at rest via the vault). Keeping the schema generic from the
start means future toggles don't need a new migration.

The flag is persisted next to the vault machinery rather than buried
in environment variables because the user's mental model is
*"I just turned it on in the UI, so new uploads should be encrypted"*
rather than *"I rebooted the API with a new env var"*.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010_app_settings"
down_revision = "0009_vault_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column(
            "updated_at",
            sa.Integer,
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
