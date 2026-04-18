"""initial schema optimizer — trajectoires_optimisees + trajectoire_pas

Revision ID: 0001
Revises:
Create Date: 2026-04-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- trajectoires_optimisees ---
    op.create_table(
        "trajectoires_optimisees",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("site_id", sa.String(64), nullable=False),
        sa.Column("timestamp_calcul", sa.DateTime(timezone=True), nullable=False),
        sa.Column("soc_initial_kwh", sa.Float(), nullable=False),
        sa.Column("statut", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("derive_pct", sa.Float(), nullable=True),
        sa.Column("horizon_debut", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon_fin", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.site_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trajectoires_optimisees_site_timestamp",
        "trajectoires_optimisees",
        ["site_id", sa.text("timestamp_calcul DESC")],
    )

    # --- trajectoire_pas ---
    op.create_table(
        "trajectoire_pas",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trajectoire_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("energie_kwh", sa.Float(), nullable=False),
        sa.Column("soc_cible_kwh", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["trajectoire_id"], ["trajectoires_optimisees.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trajectoire_pas_trajectoire_id", "trajectoire_pas", ["trajectoire_id"]
    )


def downgrade() -> None:
    op.drop_table("trajectoire_pas")
    op.drop_table("trajectoires_optimisees")
