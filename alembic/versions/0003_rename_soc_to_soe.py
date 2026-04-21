"""trajectoires_optimisees — renomme soc_initial_kwh en soe_initial_kwh

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-21

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "trajectoires_optimisees",
        "soc_initial_kwh",
        new_column_name="soe_initial_kwh",
    )


def downgrade() -> None:
    op.alter_column(
        "trajectoires_optimisees",
        "soe_initial_kwh",
        new_column_name="soc_initial_kwh",
    )
