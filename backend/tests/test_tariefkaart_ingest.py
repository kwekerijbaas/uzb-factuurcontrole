"""Tests voor het inlezen en valideren van de UZB-tariefkaart (SPEC §6).

Gevalideerd tegen de geconsolideerde kaart per 01-01-2026: 99 schalen bij Level
One, 33 bij Sterk Werk, 5 bij Cervokordaat en 10 op het jeugd-payroll-tabblad,
met 87 CAO-lonen. De validatie signaleerde daarin drie fouten die in het
Excel-bestand onopgemerkt waren gebleven.
"""

from decimal import Decimal

import pytest

from app.services.ingest.tariefkaart import cao_schaal_code
from app.services.tarief import CAT_100, CAT_135, CAT_150, TariefFactor
from app.services.tarief.validatie import (
    FOUT,
    SOORT_GEWIJZIGD,
    SOORT_NIET_UNIFORM,
    SOORT_ONTBREEKT,
    SOORT_UITSCHIETER,
    valideer_uniforme_factor,
    valideer_tarieven,
    vergelijk_factoren,
)


@pytest.mark.parametrize(
    "kaartcode,verwacht",
    [
        ("B2F", "B2"),  # Level One Flex
        ("B2V", "B2"),  # Vast -- zelfde CAO-loon
        ("B2S", "B2"),  # Seizoens
        ("B2", "B2"),  # Sterk Werk / Cervokordaat
        ("C10", "C10"),  # tweecijferige trede
        ("15B2", "15B"),  # jeugd: leeftijd + letter
        ("17C2", "17C"),
        ("Totaal", None),  # geen schaalregel
        ("", None),
    ],
)
def test_cao_schaal_code(kaartcode, verwacht):
    assert cao_schaal_code(kaartcode) == verwacht


def test_uitschieter_wordt_gevonden():
    """Reproduceert de fout in de kaart: Cervokordaat C3 had een 135%-tarief
    van 29,80 terwijl alle andere schalen 1,2455 x het basistarief hanteren."""
    tarieven = {
        "B2": {CAT_100: Decimal("29.1949"), CAT_135: Decimal("36.3611")},
        "C2": {CAT_100: Decimal("29.9491"), CAT_135: Decimal("37.3004")},
        "C3": {CAT_100: Decimal("30.8025"), CAT_135: Decimal("29.80")},  # fout
        "C4": {CAT_100: Decimal("31.7552"), CAT_135: Decimal("39.5498")},
    }
    bevindingen = valideer_tarieven("CK", tarieven)
    uitschieters = [b for b in bevindingen if b.soort == SOORT_UITSCHIETER]

    assert len(uitschieters) == 1
    assert uitschieters[0].kaartcode == "C3"
    assert uitschieters[0].ernst == FOUT
    assert "38.36" in uitschieters[0].melding  # verwachte waarde genoemd


def test_ontbrekend_tarief_wordt_gemeld():
    tarieven = {
        "B2": {CAT_100: Decimal("28.68"), CAT_150: Decimal("31.77")},
        "B3": {CAT_100: Decimal("29.04"), CAT_150: Decimal("32.16")},
        "B4": {CAT_100: Decimal("29.39"), CAT_150: Decimal("32.55")},
        "B5": {CAT_100: Decimal("29.74")},  # 150% ontbreekt
    }
    bevindingen = valideer_tarieven("SW", tarieven)
    assert [b.kaartcode for b in bevindingen if b.soort == SOORT_ONTBREEKT] == ["B5"]


def test_jeugd_en_volwassen_worden_apart_vergeleken():
    """Op één tabblad staan jeugd- en payrollschalen met eigen verhoudingen;
    door elkaar vergelijken zou valse meldingen geven."""
    tarieven = {
        "15B2": {CAT_100: Decimal("9.899"), CAT_150: Decimal("11.8147")},
        "16B2": {CAT_100: Decimal("13.6195"), CAT_150: Decimal("16.2552")},
        "17B2": {CAT_100: Decimal("17.3401"), CAT_150: Decimal("20.6958")},
        "B2F": {CAT_100: Decimal("27.3871"), CAT_150: Decimal("30.5541")},
        "C6F": {CAT_100: Decimal("31.63"), CAT_150: Decimal("35.2899")},
        "B3F": {CAT_100: Decimal("25.96"), CAT_150: Decimal("28.97")},
    }
    assert [b for b in valideer_tarieven("L1_JEUGD", tarieven)] == []


def test_uniforme_factor_signaleert_afwijkende_schaal():
    """Sterk Werk en Cervokordaat hanteren contractueel één factor per
    categorie; een afwijkende schaal is een invoerfout."""
    factoren = [
        TariefFactor("B2", "B2", CAT_100, Decimal("1.9497")),
        TariefFactor("B3", "B3", CAT_100, Decimal("1.9500")),
        TariefFactor("B4", "B4", CAT_100, Decimal("1.9503")),
        TariefFactor("B5", "B5", CAT_100, Decimal("2.1000")),  # afwijkend
    ]
    bevindingen = valideer_uniforme_factor("SW", factoren)
    assert [b.kaartcode for b in bevindingen] == ["B5"]
    assert bevindingen[0].soort == SOORT_NIET_UNIFORM


def test_vergelijk_factoren_toont_alleen_echte_wijzigingen():
    oud = [
        TariefFactor("B2F", "B2", CAT_100, Decimal("1.9674")),
        TariefFactor("B3F", "B3", CAT_100, Decimal("1.9651")),
        TariefFactor("B4F", "B4", CAT_100, Decimal("1.9635")),
    ]
    nieuw = [
        TariefFactor("B2F", "B2", CAT_100, Decimal("1.9674")),  # gelijk
        TariefFactor("B3F", "B3", CAT_100, Decimal("1.9600")),  # gedaald
        TariefFactor("B9F", "B9", CAT_100, Decimal("1.9700")),  # nieuw
    ]
    bevindingen = vergelijk_factoren("L1", oud, nieuw)
    per_code = {b.kaartcode: b.melding for b in bevindingen}

    assert "B2F" not in per_code  # ongewijzigd -> niet tonen
    assert "lager" in per_code["B3F"]
    assert "nieuw" in per_code["B9F"]
    assert "vervallen" in per_code["B4F"]
    assert all(b.soort == SOORT_GEWIJZIGD for b in bevindingen)
