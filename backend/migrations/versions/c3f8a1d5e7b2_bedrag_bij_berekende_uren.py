"""bedrag bij berekende uren

Bewaart per uitzendkracht × week ook de loonschaal en het berekende bedrag, zodat
de factuurcontrole later los kan draaien zonder de bronbestanden opnieuw in te
lezen. Zie docs/SPEC.md §7.

Revision ID: c3f8a1d5e7b2
Revises: b7c1d4e9f2a3
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3f8a1d5e7b2"
down_revision: Union[str, None] = "b7c1d4e9f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("berekende_uren", sa.Column("loonschaal", sa.String(length=50), nullable=True))
    op.add_column("berekende_uren", sa.Column("kaartcode", sa.String(length=50), nullable=True))
    op.add_column(
        "berekende_uren",
        sa.Column("minuten_per_categorie", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "berekende_uren",
        sa.Column("bedrag_per_categorie", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "berekende_uren", sa.Column("bedrag_totaal", sa.Numeric(precision=12, scale=2), nullable=True)
    )
    op.create_index(
        "ix_match_periode_week", "match_periode", ["iso_jaar", "iso_week"]
    )


def downgrade() -> None:
    op.drop_index("ix_match_periode_week", table_name="match_periode")
    for kolom in (
        "bedrag_totaal", "bedrag_per_categorie", "minuten_per_categorie",
        "kaartcode", "loonschaal",
    ):
        op.drop_column("berekende_uren", kolom)
