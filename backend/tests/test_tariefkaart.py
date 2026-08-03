"""Tests voor de afgeleide tariefkaart (SPEC §6).

De tariefkaart wordt niet ingevoerd maar berekend uit CAO-loon x omrekenfactor,
zodat een nieuwe CAO-loontabel vanaf zijn ingangsdatum automatisch de juiste
tarieven oplevert.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.tarief import (
    CAT_100,
    CAT_150,
    CAT_200,
    Loontabel,
    SchaalTarief,
    TariefFactor,
    TariefKaart,
    bouw_tariefkaart,
    kies_loontabel,
    leid_factoren_af,
)

CAO_JAN = Loontabel(
    "CAO 2026-01",
    date(2026, 1, 1),
    {"B2": Decimal("15.0000"), "B4": Decimal("16.0000")},
)
CAO_JUL = Loontabel(  # +4% loonsverhoging
    "CAO 2026-07",
    date(2026, 7, 1),
    {"B2": Decimal("15.6000"), "B4": Decimal("16.6400")},
)

FACTOREN = [
    TariefFactor("B2F", "B2", CAT_100, Decimal("1.930000")),
    TariefFactor("B2F", "B2", CAT_150, Decimal("2.261000")),
    TariefFactor("B4V", "B4", CAT_100, Decimal("1.790000")),
]


def test_tarief_is_loon_maal_factor():
    kaart, waarschuwingen = bouw_tariefkaart("L1", CAO_JAN, FACTOREN)
    assert waarschuwingen == []
    assert kaart.schaal("B2F").tarief(CAT_100) == Decimal("28.95")  # 15,00 x 1,93
    assert kaart.schaal("B4V").tarief(CAT_100) == Decimal("28.64")  # 16,00 x 1,79
    assert kaart.geldig_van == date(2026, 1, 1)


def test_nieuwe_cao_beweegt_tarieven_mee():
    """Kern van SPEC §6: alleen de lonen worden geüpload, de tarieven volgen."""
    oud, _ = bouw_tariefkaart("L1", CAO_JAN, FACTOREN)
    nieuw, _ = bouw_tariefkaart("L1", CAO_JUL, FACTOREN)

    assert nieuw.geldig_van == date(2026, 7, 1)
    assert nieuw.schaal("B2F").tarief(CAT_100) == Decimal("30.11")  # 15,60 x 1,93
    verhoging = nieuw.schaal("B2F").tarief(CAT_100) / oud.schaal("B2F").tarief(CAT_100)
    assert round(verhoging, 3) == Decimal("1.040")


def test_flex_en_vast_delen_loon_maar_niet_het_tarief():
    """Beide verwijzen naar CAO-schaal B4, met een eigen factor."""
    factoren = [
        TariefFactor("B4F", "B4", CAT_100, Decimal("1.850000")),
        TariefFactor("B4V", "B4", CAT_100, Decimal("1.790000")),
    ]
    kaart, _ = bouw_tariefkaart("L1", CAO_JAN, factoren)
    assert kaart.schaal("B4F").tarief(CAT_100) == Decimal("29.60")
    assert kaart.schaal("B4V").tarief(CAT_100) == Decimal("28.64")


def test_ontbrekend_cao_loon_geeft_waarschuwing_geen_nultarief():
    factoren = [*FACTOREN, TariefFactor("Z9F", "Z9", CAT_100, Decimal("1.9"))]
    kaart, waarschuwingen = bouw_tariefkaart("L1", CAO_JAN, factoren)
    assert kaart.schaal("Z9F") is None  # geen tarief van 0
    assert any("Z9" in w for w in waarschuwingen)


def test_leid_factoren_af_reproduceert_de_kaart():
    """Bootstrap vanaf de bestaande, met de UZB afgestemde tariefkaart."""
    origineel = TariefKaart(
        "L1",
        date(2026, 1, 1),
        None,
        {"B2F": SchaalTarief("B2F", {CAT_100: Decimal("28.94"), CAT_200: Decimal("44.97")})},
    )
    factoren, waarschuwingen = leid_factoren_af(origineel, CAO_JAN, {"B2F": "B2"})
    assert waarschuwingen == []

    herbouwd, _ = bouw_tariefkaart("L1", CAO_JAN, factoren)
    assert herbouwd.schaal("B2F").tarief(CAT_100) == Decimal("28.94")
    assert herbouwd.schaal("B2F").tarief(CAT_200) == Decimal("44.97")


@pytest.mark.parametrize(
    "dag,verwacht",
    [
        (date(2026, 6, 30), "CAO 2026-01"),
        (date(2026, 7, 1), "CAO 2026-07"),  # ingangsdatum telt zelf mee
        (date(2026, 12, 31), "CAO 2026-07"),
    ],
)
def test_kies_loontabel_volgt_ingangsdatum(dag, verwacht):
    assert kies_loontabel([CAO_JAN, CAO_JUL], dag).naam == verwacht


def test_kies_loontabel_voor_ingangsdatum_is_leeg():
    assert kies_loontabel([CAO_JUL], date(2026, 1, 1)) is None
