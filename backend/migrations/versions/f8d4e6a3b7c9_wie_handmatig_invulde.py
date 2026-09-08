"""wie handmatig invulde

Legt vast wie een handmatige loonschaal of een handmatig tarief heeft
ingevoerd. Bij een import die over zo'n waarde heen wil, toont het scherm die
naam bij de ja/nee-vraag -- dan weet degene die uploadt wie de aanpassing
maakte en waarom er om bevestiging wordt gevraagd. Zie docs/SPEC.md §4 en §6.5.

Revision ID: f8d4e6a3b7c9
Revises: e7b3c9d2a1f5
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8d4e6a3b7c9"
down_revision: Union[str, None] = "e7b3c9d2a1f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("uzk", sa.Column("schaal_door", sa.String(length=320), nullable=True))
    op.add_column(
        "uzb_tarief_handmatig", sa.Column("door", sa.String(length=320), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("uzb_tarief_handmatig", "door")
    op.drop_column("uzk", "schaal_door")
