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


# --------------------------------------------------------------------------- #
# Loontabel die midden in de week ingaat
# --------------------------------------------------------------------------- #
def _reeks(vanaf_nieuw, oud, nieuw):
    from app.services.tarief import Schaalreeks

    return Schaalreeks(((date(2026, 7, 27), oud), (vanaf_nieuw, nieuw)))


B2_JULI = SchaalTarief("B2", {CAT_100: Decimal("28.68")})
B2_AUG = SchaalTarief("B2", {CAT_100: Decimal("29.23")})


def _week(dagen):
    """Acht uur op elk van de gegeven dagen, allemaal 100%."""
    from app.services.calc.types import TraceSegment, WeekResultaat

    trace = [TraceSegment(d, 7 * 60, 15 * 60, Decimal("0"), "normaal") for d in dagen]
    return WeekResultaat(
        netto_minuten=480 * len(dagen),
        minuten_per_percentage={Decimal("0"): 480 * len(dagen)},
        trace=trace,
    )


def test_uren_lopen_tegen_het_tarief_van_hun_eigen_dag():
    """Week 31/2026 loopt van maandag 27-07 tot zondag 02-08; de nieuwe CAO gaat
    zaterdag 01-08 in. De uren van vóór die dag horen tegen het oude tarief."""
    res = _week([date(2026, 7, 30), date(2026, 7, 31), date(2026, 8, 1)])
    bedrag = bereken_bedrag(res, _reeks(date(2026, 8, 1), B2_JULI, B2_AUG), STERK_WERK)

    per_tarief = {r.tarief: r.uren for r in bedrag.regels}
    assert per_tarief == {Decimal("28.68"): Decimal("16"), Decimal("29.23"): Decimal("8")}
    assert bedrag.totaal == Decimal("16") * Decimal("28.68") + Decimal("8") * Decimal("29.23")


def test_de_ingangsdatum_staat_bij_de_regel():
    """Zonder die datum is niet te verklaren waarom één categorie twee tarieven
    heeft."""
    res = _week([date(2026, 7, 30), date(2026, 8, 1)])
    bedrag = bereken_bedrag(res, _reeks(date(2026, 8, 1), B2_JULI, B2_AUG), STERK_WERK)
    assert [r.vanaf for r in bedrag.regels] == [date(2026, 7, 27), date(2026, 8, 1)]


def test_een_week_binnen_een_periode_geeft_gewoon_een_regel():
    res = _week([date(2026, 7, 28), date(2026, 7, 29)])
    bedrag = bereken_bedrag(res, _reeks(date(2026, 8, 1), B2_JULI, B2_AUG), STERK_WERK)
    assert len(bedrag.regels) == 1
    assert bedrag.regels[0].tarief == Decimal("28.68")


def test_weektotaal_blijft_behouden_over_de_periodegrens():
    """De kwartier-afronding loopt over alle emmertjes tegelijk (SPEC §4); een
    tariefwissel mag er geen minuten bij of af doen."""
    from app.services.calc.types import TraceSegment, WeekResultaat

    trace = [
        TraceSegment(date(2026, 7, 31), 7 * 60, 15 * 60 + 7, Decimal("0"), "normaal"),
        TraceSegment(date(2026, 8, 1), 7 * 60, 15 * 60 + 8, Decimal("0"), "normaal"),
    ]
    res = WeekResultaat(netto_minuten=975, minuten_per_percentage={}, trace=trace)
    bedrag = bereken_bedrag(res, _reeks(date(2026, 8, 1), B2_JULI, B2_AUG), STERK_WERK)
    assert sum(r.minuten for r in bedrag.regels) == 975


def test_kaartreeks_levert_per_schaal_de_juiste_reeks():
    from app.services.tarief import Kaartreeks, TariefKaart

    juli = TariefKaart("SW", date(2026, 7, 1), None, {"B2": B2_JULI})
    augustus = TariefKaart("SW", date(2026, 8, 1), None, {"B2": B2_AUG})
    reeks = Kaartreeks(((date(2026, 7, 27), juli), (date(2026, 8, 1), augustus)))

    assert reeks.op(date(2026, 7, 31)) is juli
    assert reeks.op(date(2026, 8, 2)) is augustus
    assert [k.geldig_van for k in reeks.kaarten] == [date(2026, 7, 1), date(2026, 8, 1)]
    assert not reeks.is_leeg

    schalen = reeks.schalen_van("B2")
    assert schalen.op(date(2026, 7, 31)) is B2_JULI
    assert schalen.op(date(2026, 8, 1)) is B2_AUG
    assert reeks.schalen_van("Z9").op(date(2026, 8, 1)) is None
