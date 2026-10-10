"""handmatige tarieven

Tarieven die niet uit de tariefkaart komen maar met de hand in het scherm zijn
ingevoerd. Nodig voor schalen die op de kaart van het uitzendbureau ontbreken
(de Level One-kaart mist de E-schalen, waardoor die uren op EUR 0 bleven staan)
zonder op een nieuwe kaart van het bureau te hoeven wachten. Zie docs/SPEC.md §6.

Revision ID: e7b3c9d2a1f5
Revises: d5a2b7c1e9f4
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7b3c9d2a1f5"
down_revision: Union[str, None] = "d5a2b7c1e9f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "uzb_tarief_handmatig",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "uzb_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("uzb.id"), nullable=False
        ),
        sa.Column("kaartcode", sa.String(length=50), nullable=False),
        sa.Column("categorie", sa.String(length=20), nullable=False),
        sa.Column("tarief", sa.Numeric(8, 4), nullable=False),
        sa.Column("geldig_van", sa.Date(), nullable=False),
        sa.Column("geldig_tot", sa.Date(), nullable=True),
        sa.Column("aangemaakt_op", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gewijzigd_op", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_tarief_handmatig_uzb", "uzb_tarief_handmatig", ["uzb_id", "kaartcode"]
    )
    # Net als alle andere tabellen: RLS aan zonder policies, zodat de tabel
    # niet via Supabase's REST-API benaderbaar is (zie ARCHITECTURE §4b).
    op.execute("alter table uzb_tarief_handmatig enable row level security")


def downgrade() -> None:
    op.drop_index("ix_tarief_handmatig_uzb", table_name="uzb_tarief_handmatig")
    op.drop_table("uzb_tarief_handmatig")
