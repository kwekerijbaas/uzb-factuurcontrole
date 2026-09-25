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


def test_dienst_zonder_pauze_telt_gewoon_mee():
    """Nitea laat de pauzekolom leeg bij een korte dienst. Die regel eindigt dan
    na de werktijd; voorheen matchte het patroon niet en verdween de dienst
    ongemerkt uit het overzicht (week 26/2026: zes diensten bij Level One)."""
    from app.services.ingest.nitea import _REGEL

    m = _REGEL.match("6 87 - Marius Mic 27-06-2026 5:56 8:46 2:45")
    assert m is not None
    assert m.group("werk") == "2:45"
    assert m.group("pauze") is None

    met_pauze = _REGEL.match("1 87 - Marius Mic 22-06-2026 5:58 12:09 5:30 0:30")
    assert met_pauze.group("werk") == "5:30"
    assert met_pauze.group("pauze") == "0:30"


# --------------------------------------------------------------------------- #
# SNOOP-cellen in afwijkende vorm
# --------------------------------------------------------------------------- #
def test_gewerkte_uren_met_komma_tijd_of_duur():
    """Een tekstcel "8,00" of "7:45" gaf None, waarna op eind - begin werd
    teruggevallen: de pauze telde dan mee als gewerkte tijd."""
    from datetime import time as _time, timedelta

    from app.services.ingest.snoop import _als_minuten

    assert _als_minuten("8,00") == 480
    assert _als_minuten("7:45") == 465
    assert _als_minuten(_time(8, 0)) == 480
    assert _als_minuten(timedelta(hours=8)) == 480
    assert _als_minuten(8.25) == 495
    assert _als_minuten("onzin") is None


def test_snoop_datum_en_tijd_in_meer_vormen():
    from datetime import date as _date, time as _time

    from app.services.ingest.snoop import _als_datum, _als_tijd

    assert _als_datum("15/06/2026") == _date(2026, 6, 15)
    assert _als_datum("15.06.2026") == _date(2026, 6, 15)
    assert _als_tijd("24:00") == _time(0, 0)  # einde van de dag
    assert _als_tijd("25:00") is None  # onzin, geen crash


def test_snoop_voegt_hoofdletterverschillen_samen():
    """Twee schrijfwijzen van dezelfde naam leverden twee medewerkers op; na
    normaliseren bleef er één over en was de loonschaal van de ander weg."""
    import io

    from openpyxl import Workbook

    from app.services.ingest import lees_snoop

    kop = ["Registratienummer", "Medewerker", "Datum", "Starttijd", "Eindtijd",
           "Werkelijke starttijd", "Werkelijke eindtijd", "Gewerkte uren",
           "Locatie", "Werkgever op datum shift", "Type uitzendkracht",
           "Tarief uitzendbureau"]
    wb = Workbook(); ws = wb.active; ws.append(kop)
    ws.append(["1", "MARIUS MIC", "2026-06-15", "06:00", "15:00", None, None,
               "8,00", "EW4", "Level One", "Uitzendkracht", "B2 Flex"])
    ws.append(["1", "Marius Mic", "2026-06-16", "06:00", "15:00", None, None,
               "8,00", "EW4", "Level One", "Uitzendkracht", None])
    buffer = io.BytesIO(); wb.save(buffer)

    medewerkers = lees_snoop(buffer.getvalue())
    assert len(medewerkers) == 1
    assert medewerkers[0].loonschaal == "B2 Flex"
    assert len(medewerkers[0].planning) == 2


def test_snoop_meldt_onleesbare_rijen():
    import io

    from openpyxl import Workbook

    from app.services.ingest import lees_snoop

    kop = ["Medewerker", "Datum", "Starttijd", "Eindtijd"]
    wb = Workbook(); ws = wb.active; ws.append(kop)
    ws.append(["Piet Puk", "onleesbaar", "07:00", "15:30"])
    buffer = io.BytesIO(); wb.save(buffer)

    overgeslagen: list[str] = []
    assert lees_snoop(buffer.getvalue(), overgeslagen) == []
    assert overgeslagen and "Piet Puk" in overgeslagen[0]
