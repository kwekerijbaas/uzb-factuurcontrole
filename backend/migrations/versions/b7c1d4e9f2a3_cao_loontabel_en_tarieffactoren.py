"""cao loontabel en tarieffactoren

Voegt de tabellen toe waarmee de tariefkaart in het programma zelf wordt
opgebouwd: een geüploade CAO-loontabel met ingangsdatum, en per uitzendbureau
de omrekenfactoren (tarief = uurloon x factor). Zie docs/SPEC.md §6.

Revision ID: b7c1d4e9f2a3
Revises: eac2708381ee
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c1d4e9f2a3"
down_revision: Union[str, None] = "eac2708381ee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cao_loontabel",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("naam", sa.String(length=200), nullable=False),
        sa.Column("ingangsdatum", sa.Date(), nullable=False),
        sa.Column("bron_bestand", sa.String(length=500), nullable=True),
        sa.Column("geimporteerd_door", sa.UUID(), nullable=True),
        sa.Column(
            "aangemaakt_op",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "gewijzigd_op",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ingangsdatum", name="uq_cao_loontabel_ingangsdatum"),
    )

    op.create_table(
        "cao_loon",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("loontabel_id", sa.UUID(), nullable=False),
        sa.Column("schaal_code", sa.String(length=50), nullable=False),
        sa.Column("omschrijving", sa.String(length=200), nullable=True),
        sa.Column("uurloon", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.ForeignKeyConstraint(["loontabel_id"], ["cao_loontabel.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("loontabel_id", "schaal_code", name="uq_cao_loon_tabel_schaal"),
    )
    op.create_index("ix_cao_loon_schaal_code", "cao_loon", ["schaal_code"])

    op.create_table(
        "uzb_tarief_factor",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("uzb_id", sa.UUID(), nullable=False),
        sa.Column("kaartcode", sa.String(length=50), nullable=False),
        sa.Column("cao_schaal_code", sa.String(length=50), nullable=False),
        sa.Column("categorie", sa.String(length=20), nullable=False),
        sa.Column("factor", sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column("geldig_van", sa.Date(), nullable=False),
        sa.Column("geldig_tot", sa.Date(), nullable=True),
        sa.Column(
            "aangemaakt_op",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "gewijzigd_op",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["uzb_id"], ["uzb.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "uzb_id",
            "kaartcode",
            "categorie",
            "geldig_van",
            name="uq_uzb_tarief_factor_versie",
        ),
    )
    op.create_index(
        "ix_uzb_tarief_factor_uzb_geldig",
        "uzb_tarief_factor",
        ["uzb_id", "geldig_van"],
    )


def downgrade() -> None:
    op.drop_index("ix_uzb_tarief_factor_uzb_geldig", table_name="uzb_tarief_factor")
    op.drop_table("uzb_tarief_factor")
    op.drop_index("ix_cao_loon_schaal_code", table_name="cao_loon")
    op.drop_table("cao_loon")
    op.drop_table("cao_loontabel")
