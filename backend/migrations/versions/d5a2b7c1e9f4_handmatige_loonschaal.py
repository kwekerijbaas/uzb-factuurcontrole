"""handmatige loonschaal

Markeert per uitzendkracht of de loonschaal met de hand is ingevuld. Zo'n
schaal wordt niet stilzwijgend overschreven door een SNOOP-bestand of een
lijst-upload: bij een verschil volgt een melding per geval, en alleen een
bewuste keuze in het scherm neemt de bestandswaarde over. Zie docs/SPEC.md §4 (Loonschaal is verplicht).

Revision ID: d5a2b7c1e9f4
Revises: c3f8a1d5e7b2
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5a2b7c1e9f4"
down_revision: Union[str, None] = "c3f8a1d5e7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "uzk",
        sa.Column(
            "schaal_handmatig",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("uzk", "schaal_handmatig")
