"""Bevestigde CAO Glastuinbouw 1 juli 2025 t/m 31 maart 2026.

Bron: docs/cao/cao-glastuinbouw-2025-07-01_2026-03-31.pdf — artikel 28 + bijlage 1A.
Geverifieerd met Kwekerij Baas. De tijdgebonden toeslagen (onregelmatige uren)
staan hier; overwerk (35%), daggrens (>10u) en weekgrens (>48u) worden door de
engine berekend via WeekParameters.

LET OP — bewust NIET geseed (TODO, per Kwekerij Baas default uit):
- structureel werken op zondag (art. 21): zondag 06:00-15:00 max 5u toeslagvrij
- 13 aangewezen weken nacht 00:00-05:00 i.p.v. 00:00-06:00 (art. 28 lid 1c-i)
- ploegendiensttoeslagen 15% / 22% (art. 28 lid 1f)
- weekploeg-zondag 50% (art. 28 lid 1g)
- 8 weken à 50u zonder weekgrens-toeslag (art. 18 lid 4d) -> WeekParameters.week_50u_uitzondering
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.calc.types import MIN_PER_DAG, ToeslagRegel

MA_T_M_VR = frozenset({1, 2, 3, 4, 5})
MA_T_M_ZA = frozenset({1, 2, 3, 4, 5, 6})
ZA = frozenset({6})
ZO = frozenset({7})


def cao_toeslag_regels() -> list[ToeslagRegel]:
    """Standaard tijdgebonden toeslagen (onregelmatige uren) CAO Glastuinbouw."""
    return [
        # art. 28 lid 1c-i: ma t/m za 00:00-06:00 nacht/vroege ochtend
        ToeslagRegel("nacht", Decimal("50"), MA_T_M_ZA, 0, 6 * 60),
        # art. 28 lid 1c-ii: ma t/m vr 20:00-24:00 vroege avond/nacht
        ToeslagRegel("avond", Decimal("50"), MA_T_M_VR, 20 * 60, MIN_PER_DAG),
        # art. 28 lid 1c-iii: zaterdag 15:00-24:00
        ToeslagRegel("zaterdag_middag", Decimal("50"), ZA, 15 * 60, MIN_PER_DAG),
        # art. 28 lid 1e: zondag 00:00-24:00 = 100%
        ToeslagRegel("zondag", Decimal("100"), ZO, 0, MIN_PER_DAG),
        # art. 28 lid 1d: werken op feestdag = 50% (elke dag, mits feestdag)
        ToeslagRegel("feestdag", Decimal("50"), frozenset(), 0, MIN_PER_DAG, alleen_feestdag=True),
    ]


# art. 16 lid 2: doorbetaalde feestdagen (5 mei alleen in lustrumjaar, n.v.t. deze periode)
def feestdagen_2025() -> frozenset[date]:
    return frozenset(
        {
            date(2025, 12, 25),  # Eerste Kerstdag
            date(2025, 12, 26),  # Tweede Kerstdag
        }
    )


def feestdagen_2026() -> frozenset[date]:
    return frozenset(
        {
            date(2026, 1, 1),  # Nieuwjaarsdag
            date(2026, 4, 5),  # Eerste Paasdag
            date(2026, 4, 6),  # Tweede Paasdag
            date(2026, 4, 27),  # Koningsdag
            date(2026, 5, 14),  # Hemelvaartsdag
            date(2026, 5, 24),  # Eerste Pinksterdag
            date(2026, 5, 25),  # Tweede Pinksterdag
        }
    )


def feestdagen_cao_periode() -> frozenset[date]:
    """Feestdagen binnen de CAO-looptijd 1 juli 2025 t/m 31 maart 2026."""
    return frozenset(d for d in (feestdagen_2025() | feestdagen_2026()) if date(2025, 7, 1) <= d <= date(2026, 3, 31))
