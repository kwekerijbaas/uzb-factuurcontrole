"""Tests voor de tariefmapping per UZB (SPEC §5-§6).

Synthetische tarieven en uren. Gevalideerd tegen week 25/2026: Level One
reproduceert 19/21 bedragen exact (2 = andere Nitea-vintage), Sterk Werk 29/29.
"""

from datetime import date, time
from decimal import Decimal

import pytest

from app.services.calc import RegistratieRegel, WeekParameters, bereken_week
from app.services.seed.cao_glastuinbouw import cao_toeslag_regels
from app.services.tarief import (
    CAT_100,
    CAT_135,
    CAT_150,
    CAT_200,
    CAT_FEESTDAG,
    CAT_NACHTUUR,
    CERVOKORDAAT,
    LEVEL_ONE,
    STERK_WERK,
    SchaalTarief,
    TariefKaart,
    bereken_bedrag,
    kies_kaart,
)

REGELS = cao_toeslag_regels()
MA = date(2025, 9, 1)  # maandag binnen de CAO-periode
ZA = date(2025, 9, 6)
KERST = date(2025, 12, 25)  # doorbetaalde feestdag (donderdag)

L1_SCHAAL = SchaalTarief(
    "B2F",
    {
        CAT_100: Decimal("28.94"),
        CAT_150: Decimal("33.92"),
        CAT_200: Decimal("44.97"),
        CAT_FEESTDAG: Decimal("40.01"),
    },
)
SW_SCHAAL = SchaalTarief(
    "B2",
    {
        CAT_100: Decimal("28.68"),
        CAT_150: Decimal("31.77"),
        CAT_NACHTUUR: Decimal("39.28"),
        CAT_200: Decimal("42.66"),
    },
)


def week(*regels):
    return bereken_week(list(regels), [], REGELS, frozenset({KERST}), WeekParameters())


def reg(d, h1, m1, h2, m2, pauze=0):
    bruto = (h2 * 60 + m2) - (h1 * 60 + m1)
    if bruto <= 0:
        bruto += 1440
    return RegistratieRegel(d, time(h1, m1), time(h2, m2), bruto - pauze, pauze)


@pytest.mark.parametrize(
    "conv,snoop,verwacht",
    [
        (LEVEL_ONE, "B2 Flex", "B2F"),
        (LEVEL_ONE, "B4 Vast", "B4V"),
        (LEVEL_ONE, "C6 Payroll", "C6V"),  # payroll deelt het Vast-tarief
        (LEVEL_ONE, "C2 Seizoens", "C2S"),
        (STERK_WERK, "B2 Sw", "B2"),
        (STERK_WERK, "C3 SW", "C3"),  # hoofdletterongevoelig
        (CERVOKORDAAT, "C4", "C4"),  # identity
    ],
)
def test_kaartcode_mapping(conv, snoop, verwacht):
    assert conv.kaartcode(snoop) == verwacht


def test_overwerk_op_basistarief_bij_level_one():
    """135%-overwerk gaat bij Level One tegen het 100%-tarief."""
    res = week(*[reg(MA, 7, 0, 15, 0) for _ in range(5)], reg(ZA, 7, 0, 12, 0))
    bedrag = bereken_bedrag(res, L1_SCHAAL, LEVEL_ONE)
    per_cat = {r.categorie: r for r in bedrag.regels}
    assert per_cat[CAT_100].tarief == Decimal("28.94")
    assert "overwerk_35" in per_cat[CAT_100].bronnen


def test_feestdag_eigen_kolom_bij_level_one():
    res = week(reg(KERST, 7, 0, 15, 0))
    bedrag = bereken_bedrag(res, L1_SCHAAL, LEVEL_ONE)
    per_cat = {r.categorie: r for r in bedrag.regels}
    assert per_cat[CAT_FEESTDAG].tarief == Decimal("40.01")
    assert per_cat[CAT_FEESTDAG].minuten == 480


def test_feestdag_als_150_bij_sterk_werk():
    """Sterk Werk boekt feestdag op het 150%-tarief, niet op een eigen kolom."""
    res = week(reg(KERST, 7, 0, 15, 0))
    bedrag = bereken_bedrag(res, SW_SCHAAL, STERK_WERK)
    per_cat = {r.categorie: r for r in bedrag.regels}
    assert CAT_FEESTDAG not in per_cat
    assert per_cat[CAT_150].tarief == Decimal("31.77")


def test_nachtuur_apart_tarief_bij_sterk_werk():
    """Nachturen (00:00-06:00) gaan op 'Totaal nachtuur', niet op 150%."""
    res = week(reg(MA, 4, 0, 12, 0))
    bedrag = bereken_bedrag(res, SW_SCHAAL, STERK_WERK)
    per_cat = {r.categorie: r for r in bedrag.regels}
    assert per_cat[CAT_NACHTUUR].minuten == 120  # 04:00-06:00
    assert per_cat[CAT_NACHTUUR].tarief == Decimal("39.28")


def test_dag_grens_niet_gefactureerd_bij_sterk_werk_maar_uren_blijven():
    """SW belast de dag-grens (>10u/dag) niet door; die uren vallen terug op het
    basistarief in plaats van te verdwijnen."""
    res = week(reg(MA, 7, 0, 19, 0))  # 12 uur -> 2 uur boven de daggrens
    bedrag = bereken_bedrag(res, SW_SCHAAL, STERK_WERK)
    assert bedrag.minuten == res.netto_minuten  # geen uren kwijt
    assert all(r.categorie != CAT_150 for r in bedrag.regels)


def test_cervokordaat_heeft_apart_135_tarief():
    schaal = SchaalTarief(
        "C4", {CAT_100: Decimal("30.00"), CAT_135: Decimal("40.50"), CAT_150: Decimal("45.00")}
    )
    res = week(*[reg(MA, 7, 0, 15, 0) for _ in range(5)], reg(ZA, 7, 0, 12, 0))
    bedrag = bereken_bedrag(res, schaal, CERVOKORDAAT)
    per_cat = {r.categorie: r for r in bedrag.regels}
    assert per_cat[CAT_135].tarief == Decimal("40.50")


def test_ontbrekend_tarief_wordt_gemeld_niet_op_nul_gezet():
    """Een ontbrekende schaal levert géén €0-regel op (zou het gemiddelde
    vertekenen), maar een expliciete melding — SPEC §7."""
    res = week(reg(MA, 7, 0, 15, 0))
    bedrag = bereken_bedrag(res, None, LEVEL_ONE)
    assert bedrag.regels == []
    assert bedrag.totaal == Decimal("0")
    assert bedrag.ontbrekende_tarieven == [CAT_100]


def test_kies_kaart_volgt_ingangsdatum():
    """CAO-/minimumloonwijziging: elke week gebruikt de kaart die toen gold."""
    oud = TariefKaart("L1", date(2026, 1, 1), date(2026, 6, 30))
    nieuw = TariefKaart("L1", date(2026, 7, 1))
    kaarten = [oud, nieuw]
    assert kies_kaart(kaarten, date(2026, 6, 15)) is oud
    assert kies_kaart(kaarten, date(2026, 7, 2)) is nieuw
    assert kies_kaart(kaarten, date(2025, 12, 31)) is None


def test_jeugdschaal_uit_snoop_wordt_de_kaartcode():
    """SNOOP schrijft de jeugdschaal voluit ('B 17 jaar Jeugd'), de tariefkaart
    als '17B2'. Zonder deze vertaling krijgen jeugduren geen tarief."""
    from app.services.tarief import LEVEL_ONE_JEUGD

    assert LEVEL_ONE_JEUGD.kaartcode("B 17 jaar Jeugd") == "17B2"
    assert LEVEL_ONE_JEUGD.kaartcode("B 14 jaar jeugd") == "14B2"
    assert LEVEL_ONE_JEUGD.kaartcode("C 18 jaar Jeugd") == "18C2"
    # Payroll- en flexschalen op hetzelfde tabblad houden hun eigen vertaling.
    assert LEVEL_ONE_JEUGD.kaartcode("B2 Flex") == "B2F"
