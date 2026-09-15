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

from datetime import date, timedelta
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


# art. 16 lid 2: doorbetaalde feestdagen.
#
# Berekend in plaats van per jaar opgeschreven: een vaste lijst per CAO-periode
# liep af, en daarna had elke verwerkte week stilzwijgend nul feestdagen --
# de feestdagtoeslag werd dan niet berekend zonder dat iemand dat zag.
def _paaszondag(jaar: int) -> date:
    """Eerste Paasdag volgens de gregoriaanse rekenregel."""
    a = jaar % 19
    b, c = divmod(jaar, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    maand, dag = divmod(h + l - 7 * m + 90, 25)
    dag = (h + l - 7 * m + 33 * maand + 19) % 32
    return date(jaar, maand, dag)


def feestdagen_jaar(jaar: int) -> frozenset[date]:
    """De doorbetaalde feestdagen van één kalenderjaar."""
    pasen = _paaszondag(jaar)
    koningsdag = date(jaar, 4, 27)
    if koningsdag.isoweekday() == 7:  # op zondag wordt het de dag ervoor
        koningsdag = date(jaar, 4, 26)
    dagen = {
        date(jaar, 1, 1),  # Nieuwjaarsdag
        pasen,  # Eerste Paasdag
        pasen + timedelta(days=1),  # Tweede Paasdag
        koningsdag,
        pasen + timedelta(days=39),  # Hemelvaartsdag
        pasen + timedelta(days=49),  # Eerste Pinksterdag
        pasen + timedelta(days=50),  # Tweede Pinksterdag
        date(jaar, 12, 25),  # Eerste Kerstdag
        date(jaar, 12, 26),  # Tweede Kerstdag
    }
    if jaar % 5 == 0:  # Bevrijdingsdag, alleen in een lustrumjaar
        dagen.add(date(jaar, 5, 5))
    return frozenset(dagen)


def feestdagen_2025() -> frozenset[date]:
    return feestdagen_jaar(2025)


def feestdagen_2026() -> frozenset[date]:
    return feestdagen_jaar(2026)


def feestdagen_cao_periode(vandaag: date | None = None) -> frozenset[date]:
    """De feestdagen rond het heden, ruim genomen.

    Van twee jaar terug (de bewaartermijn van weekresultaten) tot een jaar
    vooruit, zodat een week aan het begin of eind van het jaar zijn feestdagen
    houdt zonder dat er iets bijgewerkt hoeft te worden.
    """
    nu = (vandaag or date.today()).year
    return frozenset().union(*(feestdagen_jaar(j) for j in range(nu - 2, nu + 2)))

