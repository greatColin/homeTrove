"""0009 vault_state table + assets encrypted columns.

Adds:

* ``vault_state`` — singleton row that holds the wrapped master key,
  the Argon2id salt + parameters, and a version stamp.
* ``assets.encrypted_path`` — path inside the vault directory that
  points to the AES-GCM encrypted payload (NULL for plain assets).
* ``assets.encrypted_nonce`` — 12-byte AES-GCM nonce used by the
  encrypted payload (NULL for plain assets).
* ``assets.origin_path`` — original plaintext path retained during the
  optional vault import phase (NULL for assets that never had a plain
  source, e.g. encrypted uploads).

Existing rows are unaffected: ``encrypted_path`` / ``encrypted_nonce``
default to NULL so every pre-migration asset remains a plain asset.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_vault_state"
down_revision = "0008_asset_filename"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vault_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kdf_salt", sa.LargeBinary, nullable=False),
        sa.Column("kdf_params_json", sa.Text, nullable=False),
        sa.Column("wrapped_master_key", sa.LargeBinary, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.Integer, nullable=False),
        sa.CheckConstraint("id = 1", name="vault_state_singleton"),
    )

    op.add_column(
        "assets",
        sa.Column("encrypted_path", sa.Text, nullable=True),
    )
    op.add_column(
        "assets",
        sa.Column("encrypted_nonce", sa.LargeBinary, nullable=True),
    )
    op.add_column(
        "assets",
        sa.Column("origin_path", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assets", "origin_path")
    op.drop_column("assets", "encrypted_nonce")
    op.drop_column("assets", "encrypted_path")
    op.drop_table("vault_state")