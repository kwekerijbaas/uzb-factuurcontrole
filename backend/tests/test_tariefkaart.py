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


# --------------------------------------------------------------------------- #
# Een nieuwe tabel noemt niet alle schalen
# --------------------------------------------------------------------------- #
def test_lonen_op_stapelt_per_schaal():
    """Per 01-07-2026 gaat alleen B1/B2 omhoog (wettelijk minimumloon). De
    overige schalen horen hun loon te houden -- anders vallen ze vanaf die dag
    zonder tarief, en dat zou een halve week stilzwijgend op nul zetten."""
    from app.services.tarief import lonen_op

    januari = Loontabel("CAO januari", date(2026, 1, 1),
                        {"B2": Decimal("14.71"), "C2": Decimal("15.09")})
    juli = Loontabel("minimumloon juli", date(2026, 7, 1), {"B2": Decimal("14.99")})

    voor = lonen_op([januari, juli], date(2026, 6, 30))
    assert (voor.loon("B2"), voor.loon("C2")) == (Decimal("14.71"), Decimal("15.09"))

    na = lonen_op([januari, juli], date(2026, 7, 1))
    assert na.loon("B2") == Decimal("14.99")
    assert na.loon("C2") == Decimal("15.09")  # niet genoemd, dus ongewijzigd
    assert na.ingangsdatum == date(2026, 7, 1)
    assert na.naam == "minimumloon juli"


def test_lonen_op_voor_de_eerste_tabel_is_leeg():
    from app.services.tarief import lonen_op

    juli = Loontabel("juli", date(2026, 7, 1), {"B2": Decimal("14.99")})
    assert lonen_op([juli], date(2026, 6, 30)) is None


def test_minimumloon_toetst_ook_de_overgenomen_schalen():
    """Doordat tabellen per schaal stapelen, blijft een fout in een oudere tabel
    doorwerken. In de tabel per 01-01-2026 stonden vijf trede-1-schalen op een
    percentage in plaats van een loon (B1 op 1,35); de CAO-PDF van 01-08-2026
    noemt trede 1 helemaal niet, dus zonder deze toets bleef dat onzichtbaar."""
    from app.services.tarief import lonen_op, valideer_minimumloon

    januari = Loontabel("januari", date(2026, 1, 1),
                        {"B1": Decimal("1.35"), "B2": Decimal("14.71")})
    augustus = Loontabel("augustus", date(2026, 8, 1), {"B2": Decimal("14.99")})

    geldend = lonen_op([januari, augustus], date(2026, 8, 1))
    bevindingen = valideer_minimumloon(geldend, Decimal("14.40"))
    assert [b.kaartcode for b in bevindingen] == ["B1"]
    # De geüploade tabel alleen zou niets melden.
    assert valideer_minimumloon(augustus, Decimal("14.40")) == []


def test_trede_een_volgt_trede_twee():
    """De CAO-loontabel per 01-08-2026 laat de regel voor trede 1 leeg; wie
    daarop staat wordt gelijk aan trede 2 beloond. Zonder deze terugval zou B1
    zonder loon en dus zonder tarief komen te zitten."""
    tabel = Loontabel("CAO", date(2026, 8, 1),
                      {"B2": Decimal("14.99"), "C2": Decimal("15.41"),
                       "G10": Decimal("23.65"), "H11": Decimal("26.85")})
    assert tabel.loon("B1") == Decimal("14.99")
    assert tabel.loon("C1") == Decimal("15.41")
    # tredes 10 en 11 zijn geen trede 1
    assert tabel.loon("G10") == Decimal("23.65")
    assert tabel.loon("H11") == Decimal("26.85")
    assert tabel.loon("Z1") is None


def test_een_ingevuld_trede_een_loon_gaat_voor():
    tabel = Loontabel("CAO", date(2026, 1, 1),
                      {"B1": Decimal("14.50"), "B2": Decimal("14.71")})
    assert tabel.loon("B1") == Decimal("14.50")


def test_gedeeltelijke_factorupload_laat_de_rest_doorlopen():
    """Level One levert soms een export met alleen de gewijzigde schalen. Alles
    afsluiten zou iedere andere schaal vanaf die datum zonder tarief zetten --
    de export van juli 2026 bevatte alleen B2 en B3, tegenover 99 kaartcodes."""
    from app.services.opslag import bewaar_factoren

    class _Rij:
        def __init__(self, kaartcode, categorie, geldig_van, geldig_tot=None):
            self.kaartcode, self.categorie = kaartcode, categorie
            self.geldig_van, self.geldig_tot = geldig_van, geldig_tot

    bestaand = [
        _Rij("B2F", CAT_100, date(2026, 1, 1)),
        _Rij("C6F", CAT_100, date(2026, 1, 1)),
    ]
    verwijderd, toegevoegd = [], []

    class _Sessie:
        def scalars(self, _):
            class R:
                @staticmethod
                def all():
                    return bestaand
            return R()
        def delete(self, rij):
            verwijderd.append(rij)
        def add(self, rij):
            toegevoegd.append(rij)
        def flush(self):
            pass

    class _Uzb:
        id = None

    nieuw = [TariefFactor("B2F", "B2", CAT_100, Decimal("1.9640"))]
    bewaar_factoren(_Sessie(), _Uzb(), nieuw, date(2026, 7, 1), volledig=False)

    assert bestaand[0].geldig_tot == date(2026, 6, 30)  # B2F vervangen
    assert bestaand[1].geldig_tot is None  # C6F loopt gewoon door
    assert len(toegevoegd) == 1


def test_handmatig_tarief_wint_en_vult_de_kaart_aan():
    """De Level One-kaart mist de E-schalen; zonder handmatig tarief blijven
    die uren op EUR 0. Handmatig wint ook van een afgeleid tarief."""
    from app.services.tarief.types import SchaalTarief, TariefKaart

    kaart = TariefKaart("L1", date(2026, 1, 1), None,
                        {"B2F": SchaalTarief("B2F", {CAT_100: Decimal("28.94")})})
    handmatig = {"E5V": {CAT_100: Decimal("34.79"), CAT_150: Decimal("38.55")},
                 "B2F": {CAT_100: Decimal("30.00")}}
    # zelfde samenvoeging als opslag.kaart_op
    for code, tarieven in handmatig.items():
        bestaande = dict(kaart.schalen.get(code, SchaalTarief(code, {})).tarieven)
        bestaande.update(tarieven)
        kaart.schalen[code] = SchaalTarief(code, bestaande)

    assert kaart.schaal("E5V").tarief(CAT_100) == Decimal("34.79")
    assert kaart.schaal("E5V").tarief(CAT_150) == Decimal("38.55")
    assert kaart.schaal("B2F").tarief(CAT_100) == Decimal("30.00")  # handmatig wint
