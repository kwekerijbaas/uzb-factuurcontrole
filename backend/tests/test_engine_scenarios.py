"""Validatie-scenario's voor de CAO Glastuinbouw calculatie-engine.
Bedragen in minuten zodat er geen float-onnauwkeurigheid in de asserts sluipt."""

from datetime import date, time
from decimal import Decimal

import pytest

from app.services.calc import (
    PlanningRegel,
    RegistratieRegel,
    WeekParameters,
    bereken_week,
)
from app.services.calc.types import (
    SOORT_TIJD_VERSCHIL,
    SOORT_UREN_VERSCHIL,
)
from app.services.seed.cao_glastuinbouw import cao_toeslag_regels, feestdagen_cao_periode

REGELS = cao_toeslag_regels()
FEEST = feestdagen_cao_periode()

# vaste data binnen de CAO-periode
MA = date(2025, 9, 1)  # maandag
DI = date(2025, 9, 2)
WO = date(2025, 9, 3)
DO = date(2025, 9, 4)
VR = date(2025, 9, 5)
ZA = date(2025, 9, 6)
ZO = date(2025, 9, 7)
KERST = date(2025, 12, 25)  # donderdag, doorbetaalde feestdag


def reg(d, h1, m1, h2, m2, pauze=0):
    bruto = (h2 * 60 + m2) - (h1 * 60 + m1)
    if bruto <= 0:
        bruto += 1440
    return RegistratieRegel(d, time(h1, m1), time(h2, m2), bruto - pauze, pauze)


def buckets(res):
    return {pct: m for pct, m in res.minuten_per_percentage.items() if m}


def test_1_doordeweekse_dag_exact_volgens_planning():
    r = [reg(MA, 8, 0, 16, 0)]
    p = [PlanningRegel(MA, time(8, 0), time(16, 0), 480)]
    res = bereken_week(r, p, REGELS, FEEST)
    assert buckets(res) == {Decimal("0"): 480}
    assert res.afwijkingen == []


def test_2_avondtoeslag():
    # 14:00-23:00 -> 14-20 dag (0%), 20-23 avond (50%)
    res = bereken_week([reg(MA, 14, 0, 23, 0)], [], REGELS, FEEST)
    assert buckets(res) == {Decimal("0"): 360, Decimal("50"): 180}


def test_3_zaterdag_middagtoeslag():
    # za 08:00-16:00 -> 08-15 dag (0%), 15-16 toeslag (50%)
    res = bereken_week([reg(ZA, 8, 0, 16, 0)], [], REGELS, FEEST)
    assert buckets(res) == {Decimal("0"): 420, Decimal("50"): 60}


def test_4_zondag_100():
    res = bereken_week([reg(ZO, 8, 0, 12, 0)], [], REGELS, FEEST)
    assert buckets(res) == {Decimal("100"): 240}


def test_5_feestdag_50():
    # Eerste Kerstdag, doordeweeks, gewerkt 08:00-16:00 -> hele dag 50%
    res = bereken_week([reg(KERST, 8, 0, 16, 0)], [], REGELS, FEEST)
    assert buckets(res) == {Decimal("50"): 480}


def test_6_overwerk_42uur_week():
    # 42u-week, norm 38u -> 4u (240 min) overwerk @35%, rest @0%
    r = [reg(MA, 6, 0, 14, 0), reg(DI, 6, 0, 14, 0), reg(WO, 6, 0, 14, 0),
         reg(DO, 6, 0, 14, 0), reg(VR, 6, 0, 16, 0)]  # 8+8+8+8+10 = 42u
    res = bereken_week(r, [], REGELS, FEEST)
    assert buckets(res) == {Decimal("0"): 2280, Decimal("35"): 240}


def test_7_dag_grens_boven_10_uur():
    # 1 dag 06:00-18:00 = 12u -> 10u @0%, laatste 2u @50% (daggrens)
    res = bereken_week([reg(MA, 6, 0, 18, 0)], [], REGELS, FEEST)
    assert buckets(res) == {Decimal("0"): 600, Decimal("50"): 120}


def test_8_nachtdienst_over_middernacht():
    # ma 22:00 -> di 02:00: ma 22-24 avond (50%), di 00-02 nacht (50%)
    res = bereken_week([reg(MA, 22, 0, 2, 0)], [], REGELS, FEEST)
    assert buckets(res) == {Decimal("50"): 240}


def test_9_pauze_valt_op_laagste_toeslag():
    # 17:00-22:00 (span 300), pauze 60 -> pauze gaat van 0%-deel af
    # netto 240: 17-20 = 180 - 60 pauze = 120 @0%, 20-22 = 120 @50%
    res = bereken_week([reg(MA, 17, 0, 22, 0, pauze=60)], [], REGELS, FEEST)
    assert buckets(res) == {Decimal("0"): 120, Decimal("50"): 120}
    assert res.netto_minuten == 240


def test_10_hoogste_wint_zondag_overschrijft_overwerk():
    # 38u ma-vr (norm vol), daarna zondag 4u: zondag-uren moeten 100% blijven,
    # niet 100+35. Geen cumulatie (art. 28 lid 2b).
    base = [reg(MA, 6, 0, 14, 0), reg(DI, 6, 0, 14, 0), reg(WO, 6, 0, 14, 0),
            reg(DO, 6, 0, 14, 0), reg(VR, 6, 0, 12, 0)]  # 8*4 + 6 = 38u
    zondag = [reg(ZO, 8, 0, 12, 0)]  # 4u, valt boven de weeknorm
    res = bereken_week(base + zondag, [], REGELS, FEEST)
    b = buckets(res)
    assert b.get(Decimal("100")) == 240  # zondag blijft 100%, niet 135%
    assert Decimal("135") not in b
    assert b.get(Decimal("0")) == 2280  # de 38u norm-uren


def test_11_consistentie_check_flag():
    # Nitea meldt 400 gewerkte minuten maar begin/eind-pauze klopt niet
    bad = RegistratieRegel(MA, time(8, 0), time(16, 0), 400, 0)  # zou 480 moeten zijn
    res = bereken_week([bad], [], REGELS, FEEST)
    soorten = {a.soort for a in res.afwijkingen}
    assert "registratie_inconsistent" in soorten


def test_12_planning_afwijkingen():
    r = [reg(MA, 8, 0, 17, 0)]  # 9u gewerkt
    p = [PlanningRegel(MA, time(8, 0), time(16, 0), 480)]  # 8u gepland
    res = bereken_week(r, p, REGELS, FEEST)
    soorten = {a.soort for a in res.afwijkingen}
    assert SOORT_UREN_VERSCHIL in soorten
    assert SOORT_TIJD_VERSCHIL in soorten


def test_13_week_50u_uitzondering_geen_weekgrenstoeslag():
    # 50u-week (art. 18 lid 4d): geen 50% voor uren > 48u, wel 35% overwerk
    r = [reg(MA, 6, 0, 16, 0), reg(DI, 6, 0, 16, 0), reg(WO, 6, 0, 16, 0),
         reg(DO, 6, 0, 16, 0), reg(VR, 6, 0, 16, 0)]  # 5 * 10u = 50u
    params = WeekParameters(week_50u_uitzondering=True)
    res = bereken_week(r, [], REGELS, FEEST, params)
    b = buckets(res)
    # 38u norm @0%, 12u (720) overwerk @35%, geen 50%
    assert b == {Decimal("0"): 2280, Decimal("35"): 720}


def test_13b_week_grens_50_zonder_uitzondering():
    # zelfde 50u-week, nu zonder uitzondering -> uren > 48u krijgen 50%
    r = [reg(MA, 6, 0, 16, 0), reg(DI, 6, 0, 16, 0), reg(WO, 6, 0, 16, 0),
         reg(DO, 6, 0, 16, 0), reg(VR, 6, 0, 16, 0)]  # 50u
    res = bereken_week(r, [], REGELS, FEEST)
    b = buckets(res)
    # 38u @0, 48u-grens: 2 laatste uren (120) @50, tussenliggende 10u @35
    assert b.get(Decimal("50")) == 120  # uren 48->50
    assert b.get(Decimal("35")) == 600  # uren 38->48
    assert b.get(Decimal("0")) == 2280


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
