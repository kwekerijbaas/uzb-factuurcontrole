"""ontbrekende minuten bij de berekende uren

Legt per bewaarde week vast welke uren geen tarief kregen doordat de
tariefkaart een kolom mist. Zonder dit ziet de factuurcontrole dagen later
alleen een te laag bedrag en spreekt zij het uitzendbureau aan op een tekort
dat aan onze kant zit. Zie docs/SPEC.md paragraaf 4.

Revision ID: b4e8c2a6d0f3
Revises: a9c2e4f6b8d1
Create Date: 2026-09-15 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b4e8c2a6d0f3"
down_revision: Union[str, None] = "a9c2e4f6b8d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "berekende_uren",
        sa.Column("ontbrekende_minuten", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("berekende_uren", "ontbrekende_minuten")
