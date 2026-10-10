"""apart gefactureerd

Markeert uitzendkrachten die het bureau los factureert (bv. techniek, apart
geboekt). Zij krijgen een eigen weekoverzicht en een eigen factuurcontrole,
zodat het hoofdoverzicht naast de hoofdfactuur blijft passen. Zie
docs/SPEC.md §4.

Revision ID: a9c2e4f6b8d1
Revises: f8d4e6a3b7c9
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9c2e4f6b8d1"
down_revision: Union[str, None] = "f8d4e6a3b7c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "uzk",
        sa.Column(
            "apart_gefactureerd", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("uzk", sa.Column("apart_door", sa.String(length=320), nullable=True))


def downgrade() -> None:
    op.drop_column("uzk", "apart_door")
    op.drop_column("uzk", "apart_gefactureerd")
