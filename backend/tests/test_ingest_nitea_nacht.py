"""Tests voor nachtdiensten en niet-leesbare regels in het Nitea-overzicht.

Aanleiding: week 32/2026 Level One. Sylwia Piatek draaide nachtdiensten en
kwam op 1,75 uur uit: twee regels waren verschoven gelezen ('14:57-08:00 met
60 gewerkte minuten') en de overige nachten ontbraken helemaal, zonder dat het
overzicht daar iets van liet zien.
"""

from datetime import date, time
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

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
