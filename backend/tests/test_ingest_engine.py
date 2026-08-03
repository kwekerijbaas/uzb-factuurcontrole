"""Tests voor de Nitea-'Werk tijd'-leidend correctie (split shifts) en de
kwartier-afronding. Synthetische invoer; gevalideerd tegen week 25/2026 waar
deze twee stappen het oude overzicht voor 18/21 medewerkers exact reproduceren."""

from datetime import date, time
from decimal import Decimal

from app.services.calc import RegistratieRegel, WeekParameters, bereken_week
from app.services.calc.engine import rond_op_kwartier
from app.services.seed.cao_glastuinbouw import cao_toeslag_regels

REGELS = cao_toeslag_regels()
MA = date(2025, 9, 1)  # maandag, binnen CAO-periode


def test_split_shift_gebruikt_nitea_werktijd():
    """Bracket 07:58–20:34 (756 min) met slechts 345 min Nitea-werktijd: de
    niet-gewerkte tijd (pauze + onderbreking) wordt uit de laagste toeslag
    gehaald, avonduren (20:00–20:34 = 50%) blijven behouden."""
    reg = RegistratieRegel(MA, time(7, 58), time(20, 34), gewerkte_minuten=345, pauze_minuten=60)
    res = bereken_week([reg], [], REGELS, frozenset(), WeekParameters())

    assert res.netto_minuten == 345  # exact de Nitea-werktijd, niet 756-60
    assert res.minuten_per_percentage.get(Decimal("50")) == 34  # avond behouden
    assert res.minuten_per_percentage.get(Decimal("0")) == 311


def test_continue_dienst_ongewijzigd():
    """Zonder onderbreking is bracket − pauze == werktijd; gedrag onveranderd."""
    reg = RegistratieRegel(MA, time(7, 0), time(15, 30), gewerkte_minuten=450, pauze_minuten=60)
    res = bereken_week([reg], [], REGELS, frozenset(), WeekParameters())
    assert res.netto_minuten == 450
    assert res.minuten_per_percentage.get(Decimal("0")) == 450


def test_rond_op_kwartier_behoudt_totaal():
    ruw = {Decimal("0"): 2220, Decimal("35"): 249, Decimal("50"): 156}  # 43,75u
    af = rond_op_kwartier(ruw)
    assert af[Decimal("35")] == 255  # 4,25u
    assert af[Decimal("50")] == 150  # 2,50u
    assert sum(af.values()) == sum(ruw.values())  # weektotaal behouden


def test_rond_op_kwartier_verwijdert_sliver():
    ruw = {Decimal("0"): 2277, Decimal("50"): 3}  # 38,00u met 3 min nacht-sliver
    af = rond_op_kwartier(ruw)
    assert Decimal("50") not in af  # sliver weggerond
    assert af[Decimal("0")] == 2280
    assert sum(af.values()) == 2280
