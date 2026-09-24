"""Tests voor nachtdiensten en niet-leesbare regels in het Nitea-overzicht.

Aanleiding: week 32/2026 Level One. Sylwia Piatek draaide nachtdiensten en
kwam op 1,75 uur uit: twee regels waren verschoven gelezen ('14:57-08:00 met
60 gewerkte minuten') en de overige nachten ontbraken helemaal, zonder dat het
overzicht daar iets van liet zien.
"""

from datetime import date, time
from decimal import Decimal
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.calc.types import RegistratieRegel

from app.services.ingest.nitea import _REGEL, _regel_uit, lees_nitea


def _pdf(regels: list[str]) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 8)
    y = 800
    for regel in ["Medewerker uren", "Periode 03-08-2026 t/m 09-08-2026", *regels]:
        c.drawString(30, y, regel)
        y -= 12
    c.save()
    return buf.getvalue()


def test_nachtdienst_met_einddatum_wordt_gelezen():
    m = _REGEL.match("3 771 - Sylwia Piatek 03-08-2026 22:57 04-08-2026 8:00 8:00 1:00")
    assert m is not None
    regel, opmerking = _regel_uit(m)
    assert (regel.datum, regel.begin, regel.eind) == (date(2026, 8, 3), time(22, 57), time(8, 0))
    assert (regel.gewerkte_minuten, regel.pauze_minuten) == (480, 60)
    assert opmerking is None


def test_ontbrekende_eindtijd_wordt_uit_werktijd_afgeleid():
    """'14:57 8:00 1:00' zonder eindtijd: niet een dienst van 17 uur met één
    gewerkt uur, maar begin + werk + pauze."""
    regel, opmerking = _regel_uit(_REGEL.match("5 771 - Sylwia Piatek 03-08-2026 14:57 8:00 1:00"))
    assert regel.eind == time(23, 57)
    assert (regel.gewerkte_minuten, regel.pauze_minuten) == (480, 60)
    assert "geen eindtijd" in opmerking and "23:57" in opmerking

    regel, _ = _regel_uit(_REGEL.match("6 771 - Sylwia Piatek 07-08-2026 6:07 5:15 0:45"))
    assert regel.eind == time(12, 7)
    assert regel.gewerkte_minuten == 315


def test_korte_dienst_zonder_pauze_blijft_gewoon_een_dienst():
    """Drie tijden waarvan begin-einde de werktijd wél verklaart: niets aan
    veranderen (de bestaande lezing uit week 26)."""
    regel, opmerking = _regel_uit(_REGEL.match("6 87 - Marius Mic 27-06-2026 5:56 8:46 2:45"))
    assert (regel.begin, regel.eind, regel.gewerkte_minuten) == (time(5, 56), time(8, 46), 165)
    assert opmerking is None


def test_onleesbare_registratieregel_wordt_gemeld():
    """Een regel die op een registratie lijkt maar niet te lezen is, mag niet
    stil verdwijnen: dat is een te laag weektotaal dat niemand ziet."""
    overgeslagen: list[str] = []
    medewerkers = lees_nitea(
        _pdf([
            "1 87 - Marius Mic 03-08-2026 6:59 16:02 7:45 1:15",
            "2 771 - Sylwia Piatek 04-08-2026 22:57 -- 8:00 1:00 nacht",
            "3 771 - Sylwia Piatek 05-08-2026 22:57 06-08-2026 7:00 7:00 1:00",
            "4 771 - Sylwia Piatek 06-08-2026 14:57 8:00 1:00",
        ]),
        overgeslagen,
    )
    assert [m.naam for m in medewerkers] == ["Marius Mic", "Sylwia Piatek"]
    assert len(medewerkers[1].registratie) == 2
    assert any(o.startswith("2 771 - Sylwia Piatek 04-08-2026") for o in overgeslagen)
    assert any("geen eindtijd" in o for o in overgeslagen)
    assert len(overgeslagen) == 2


# --------------------------------------------------------------------------- #
# Lege begin- en eindtijd bij nacht- en middagdiensten
# --------------------------------------------------------------------------- #
def _nitea_pdf(rijen: list[tuple]) -> bytes:
    """Het overzicht 'Medewerker uren' met zijn echte kolomindeling.

    De cellen Begin tijd en Einde tijd blijven bij nacht- en middagdiensten
    leeg; welke tijd waar hoort is dan alleen aan de kolom te zien.
    """
    kolom = {"nr": 40, "mw": 70, "datum": 250, "begin": 360, "eind": 430,
             "werk": 500, "pauze": 560}
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 9)
    y = 780
    c.drawString(kolom["nr"], y, "Nr")
    c.drawString(kolom["mw"], y, "Medewerker")
    c.drawString(kolom["datum"], y, "Datum")
    for sleutel, kop in (("begin", "Begin"), ("eind", "Einde"),
                         ("werk", "Werk"), ("pauze", "Pauze")):
        c.drawString(kolom[sleutel], y, kop + " tijd")
    y -= 16
    for nr, mw, datum, begin, eind, werk, pauze in rijen:
        c.drawString(kolom["nr"], y, str(nr))
        c.drawString(kolom["mw"], y, mw)
        c.drawString(kolom["datum"], y, datum)
        for sleutel, waarde in (("begin", begin), ("eind", eind),
                                ("werk", werk), ("pauze", pauze)):
            if waarde:
                c.drawRightString(kolom[sleutel] + 40, y, waarde)
        y -= 14
    c.save()
    return buf.getvalue()


# Week 32/2026 Sterk Werk, zoals Nitea hem afdrukte.
WEEK_MET_LEGE_TIJDEN = [
    (1, "400 - Cornelia Nadina Calota", "03-08-2026", "14:58", "", "8:00", "1:00"),
    (2, "400 - Cornelia Nadina Calota", "04-08-2026", "", "", "8:00", "1:00"),
    (3, "400 - Cornelia Nadina Calota", "05-08-2026", "", "", "5:30", "0:30"),
    (4, "400 - Cornelia Nadina Calota", "06-08-2026", "", "", "10:45", "1:15"),
    (5, "400 - Cornelia Nadina Calota", "07-08-2026", "", "6:07", "5:15", "0:45"),
    (6, "410 - Claudiu Dragan", "03-08-2026", "7:00", "15:05", "7:00", "1:00"),
]


def test_dagen_zonder_tijden_tellen_gewoon_mee():
    """Drie van de vijf dagen hadden geen begin- én eindtijd. Die regels
    verdwenen volledig: 24,25 uur telde niet mee in de week en dus ook niet in
    de factuurcontrole."""
    overgeslagen: list[str] = []
    medewerkers = lees_nitea(_nitea_pdf(WEEK_MET_LEGE_TIJDEN), overgeslagen)
    cornelia = next(m for m in medewerkers if m.naam.startswith("Cornelia"))

    assert len(cornelia.registratie) == 5
    assert sum(r.gewerkte_minuten for r in cornelia.registratie) == 2250  # 37,50 u
    zonder = [r for r in cornelia.registratie if not r.tijden_bekend]
    assert [r.datum.day for r in zonder] == [4, 5, 6]
    assert overgeslagen  # elke dag zonder tijden wordt gemeld
    assert all("telt mee" in o or "tellen mee" in o or "gelezen als" in o
               for o in overgeslagen)


def test_losse_eindtijd_wordt_niet_als_begintijd_gelezen():
    """Op 07-08 staat alleen een eindtijd (6:07). Die werd als begintijd
    gelezen, waardoor een nachtdienst als dagdienst werd afgerekend."""
    medewerkers = lees_nitea(_nitea_pdf(WEEK_MET_LEGE_TIJDEN))
    cornelia = next(m for m in medewerkers if m.naam.startswith("Cornelia"))
    zevende = next(r for r in cornelia.registratie if r.datum.day == 7)

    assert zevende.eind == time(6, 7)
    assert zevende.begin == time(0, 7)  # 6:07 min 5:15 werk min 0:45 pauze


def test_volledige_regels_blijven_gewoon_gelezen():
    medewerkers = lees_nitea(_nitea_pdf(WEEK_MET_LEGE_TIJDEN))
    claudiu = next(m for m in medewerkers if m.naam.startswith("Claudiu"))
    regel = claudiu.registratie[0]
    assert (regel.begin, regel.eind) == (time(7, 0), time(15, 5))
    assert (regel.gewerkte_minuten, regel.pauze_minuten) == (420, 60)


def test_week_zonder_tijden_telt_de_uren_en_meldt_de_dag():
    """De uren tellen mee tegen het basistarief; zonder klok is geen toeslag
    vast te stellen, en dat hoort zichtbaar te zijn."""
    from datetime import date as _date

    from app.services.calc import bereken_week
    from app.services.seed.cao_glastuinbouw import cao_toeslag_regels, feestdagen_cao_periode

    medewerkers = lees_nitea(_nitea_pdf(WEEK_MET_LEGE_TIJDEN))
    cornelia = next(m for m in medewerkers if m.naam.startswith("Cornelia"))
    resultaat = bereken_week(
        cornelia.registratie, [], cao_toeslag_regels(),
        feestdagen_cao_periode(_date(2026, 8, 5)),
    )
    assert resultaat.netto_uren == Decimal("37.50")
    zonder_tijden = [
        a for a in resultaat.afwijkingen if "zonder begin- en eindtijd" in a.detail
    ]
    assert len(zonder_tijden) == 3
    assert "tellen mee" in zonder_tijden[0].detail


# --------------------------------------------------------------------------- #
# Terugval op de SNOOP-planning voor de tijdgebonden toeslag
# --------------------------------------------------------------------------- #
def test_ontbrekende_tijden_vallen_terug_op_de_snoop_planning():
    """Ola's kernprobleem: Nitea laat bij nacht- en middagdiensten de tijden
    leeg, waardoor er geen toeslag werd berekend en de factuur handmatig moest.
    SNOOP kent de dienst wel; de klok komt dan daarvandaan, de uren blijven van
    Nitea."""
    from app.services.calc import PlanningRegel, bereken_week
    from app.services.seed.cao_glastuinbouw import cao_toeslag_regels, feestdagen_cao_periode

    registratie = [RegistratieRegel(date(2026, 8, 4), None, None, 480, 60)]
    planning = [PlanningRegel(date(2026, 8, 4), time(15, 0), time(23, 30), 480)]
    resultaat = bereken_week(
        registratie, planning, cao_toeslag_regels(), feestdagen_cao_periode(date(2026, 8, 4)),
    )
    assert resultaat.netto_uren == Decimal("8.00")
    assert resultaat.minuten_per_percentage.get(Decimal("50"), 0) > 0  # avondtoeslag
    melding = next(a for a in resultaat.afwijkingen if a.datum == date(2026, 8, 4))
    assert "overgenomen uit de SNOOP-planning" in melding.detail
    assert "15:00-23:30" in melding.detail


def test_meerdere_geplande_diensten_zijn_te_dubbelzinnig():
    """Twee geplande diensten op één dag: niet gokken welke het was."""
    from app.services.calc import PlanningRegel, bereken_week
    from app.services.seed.cao_glastuinbouw import cao_toeslag_regels, feestdagen_cao_periode

    registratie = [RegistratieRegel(date(2026, 8, 4), None, None, 240, 0)]
    planning = [
        PlanningRegel(date(2026, 8, 4), time(6, 0), time(10, 0), 240),
        PlanningRegel(date(2026, 8, 4), time(15, 0), time(19, 0), 240),
    ]
    resultaat = bereken_week(
        registratie, planning, cao_toeslag_regels(), feestdagen_cao_periode(date(2026, 8, 4)),
    )
    assert resultaat.netto_uren == Decimal("4.00")
    assert resultaat.minuten_per_percentage == {Decimal("0"): 240}  # geen toeslag toegekend
    melding = next(a for a in resultaat.afwijkingen if a.datum == date(2026, 8, 4))
    assert "meerdere geplande diensten" in melding.detail


def test_planning_korter_dan_de_gewerkte_tijd_wordt_niet_gebruikt():
    """Werkte iemand langer dan gepland, dan past de Nitea-tijd niet in de
    planning-bracket; de terugval blijft dan achterwege."""
    from app.services.calc import PlanningRegel, bereken_week
    from app.services.seed.cao_glastuinbouw import cao_toeslag_regels, feestdagen_cao_periode

    registratie = [RegistratieRegel(date(2026, 8, 4), None, None, 600, 0)]  # 10 uur
    planning = [PlanningRegel(date(2026, 8, 4), time(15, 0), time(19, 0), 240)]  # 4 uur
    resultaat = bereken_week(
        registratie, planning, cao_toeslag_regels(), feestdagen_cao_periode(date(2026, 8, 4)),
    )
    assert resultaat.netto_uren == Decimal("10.00")
    assert resultaat.minuten_per_percentage == {Decimal("0"): 600}
    melding = next(a for a in resultaat.afwijkingen if a.datum == date(2026, 8, 4))
    assert "korter dan de gewerkte tijd" in melding.detail


def test_geen_planning_die_dag_blijft_zonder_toeslag_zoals_eerst():
    from app.services.calc import bereken_week
    from app.services.seed.cao_glastuinbouw import cao_toeslag_regels, feestdagen_cao_periode

    registratie = [RegistratieRegel(date(2026, 8, 4), None, None, 480, 60)]
    resultaat = bereken_week(
        registratie, [], cao_toeslag_regels(), feestdagen_cao_periode(date(2026, 8, 4)),
    )
    assert resultaat.minuten_per_percentage == {Decimal("0"): 480}
    melding = next(a for a in resultaat.afwijkingen if a.datum == date(2026, 8, 4))
    assert "geen (passende) planning" in melding.detail


def test_week_met_planning_fallback_via_verwerk_week():
    """End-to-end: de SNOOP-planning van dezelfde medewerker bereikt de engine
    ook via verwerk_week, zonder dat vergelijk_planning aan hoeft te staan."""
    from app.services.calc.types import PlanningRegel as PR
    from app.services.ingest import NiteaMedewerker, SnoopMedewerker
    from app.services.seed.cao_glastuinbouw import cao_toeslag_regels, feestdagen_cao_periode
    from app.services.tarief import CAT_100, CAT_150, LEVEL_ONE, SchaalTarief, TariefKaart
    from app.services.verwerking import verwerk_week

    kaart = TariefKaart(
        "L1", date(2026, 1, 1), None,
        {"B2F": SchaalTarief("B2F", {CAT_100: Decimal("28.94"), CAT_150: Decimal("33.92")})},
    )
    nitea = [NiteaMedewerker(
        naam="Nacht Werker", nitea_id="1",
        registratie=[RegistratieRegel(date(2026, 8, 4), None, None, 480, 60)],
    )]
    snoop = [SnoopMedewerker(
        naam="Nacht Werker", loonschaal="B2 Flex",
        planning=[PR(date(2026, 8, 4), time(15, 0), time(23, 30), 480)],
    )]
    verwerking = verwerk_week(
        "L1", 2026, 32, snoop, nitea, cao_toeslag_regels(), kaart, LEVEL_ONE,
        feestdagen=feestdagen_cao_periode(date(2026, 8, 4)),
    )
    medewerker = verwerking.medewerkers[0]
    assert medewerker.netto_uren == Decimal("8.00")
    assert medewerker.bedrag.totaal > Decimal("8") * Decimal("28.94")  # avondtoeslag telt mee
    assert any("SNOOP-planning" in m for m in verwerking.meldingen) or any(
        "SNOOP-planning" in a.detail for a in medewerker.afwijkingen
    )
