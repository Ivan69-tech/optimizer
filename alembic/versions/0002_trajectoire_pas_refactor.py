"""trajectoire_pas — table glissante par (site_id, timestamp)

Supprime id et trajectoire_id, ajoute site_id et insertion_timestamp.
La PK devient (site_id, timestamp).

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_trajectoire_pas_trajectoire_id", table_name="trajectoire_pas")
    op.drop_table("trajectoire_pas")

    op.create_table(
        "trajectoire_pas",
        sa.Column("site_id", sa.String(64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("energie_kwh", sa.Float(), nullable=False),
        sa.Column("soe_cible_kwh", sa.Float(), nullable=False),
        sa.Column("insertion_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.site_id"]),
        sa.PrimaryKeyConstraint("site_id", "timestamp"),
    )
    op.create_index(
        "ix_trajectoire_pas_site_timestamp",
        "trajectoire_pas",
        ["site_id", sa.text("timestamp DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_trajectoire_pas_site_timestamp", table_name="trajectoire_pas")
    op.drop_table("trajectoire_pas")

    op.create_table(
        "trajectoire_pas",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trajectoire_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("energie_kwh", sa.Float(), nullable=False),
        sa.Column("soe_cible_kwh", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["trajectoire_id"], ["trajectoires_optimisees.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trajectoire_pas_trajectoire_id", "trajectoire_pas", ["trajectoire_id"]
    )
