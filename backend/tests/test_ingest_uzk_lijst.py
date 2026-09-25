"""Tests voor de uitzendkrachtenlijst.

Gevalideerd tegen de lijsten over 2026: Sterk Werk 104 uitzendkrachten (10 met
een schaalwissel), Level One 300 (298 met loonschaal, 3 gewisseld).
"""

from datetime import datetime
from io import BytesIO

import pytest
from openpyxl import Workbook

from app.services.ingest.uzk_lijst import lees_uzk_lijst

_KOP = [
    "Registratienummer", "Medewerker", "Datum", "Starttijd", "Eindtijd",
    "Werkelijke starttijd", "Werkelijke eindtijd", "Gewerkte uren", "Locatie",
    "Werkgever op datum shift", "Type uitzendkracht", "Tarief uitzendbureau",
]


def _bestand(rijen) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(_KOP)
    for code, naam, dag, schaal in rijen:
        ws.append(
            [code, naam, datetime.fromisoformat(dag), "07:00", "15:00", None, None,
             7.5, "EW5", "Level One", "Uitzendkracht", schaal]
        )
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_een_regel_per_medewerker():
    bron = _bestand(
        [
            ("100", "Marius Mic", "2026-02-01", "B2 Flex"),
            ("100", "Marius Mic", "2026-02-02", "B2 Flex"),
            ("200", "Elena Grasu", "2026-02-01", "C3 SW"),
        ]
    )
    regels, _ = lees_uzk_lijst(bron)
    assert [r.naam for r in regels] == ["Elena Grasu", "Marius Mic"]
    assert regels[1].externe_code == "100"


def test_bij_schaalwissel_telt_de_laatste():
    """Een schaal kan in de loop van het jaar wijzigen; de meest recente geldt,
    niet de meest voorkomende."""
    bron = _bestand(
        [("1", "Vlad Dragoi", f"2026-02-{d:02d}", "B2 Sw") for d in range(1, 20)]
        + [("1", "Vlad Dragoi", "2026-06-01", "C3 SW")]
    )
    regels, waarschuwingen = lees_uzk_lijst(bron)

    assert regels[0].loonschaal == "C3 SW"
    assert regels[0].eerdere_schalen == ["B2 Sw"]
    assert regels[0].is_gewisseld
    assert any("gewisseld" in w for w in waarschuwingen)


def test_medewerker_zonder_schaal_wordt_gemeld():
    bron = _bestand(
        [("1", "Julian de Olde", "2026-03-07", None), ("2", "Marius Mic", "2026-03-07", "B2 Flex")]
    )
    regels, waarschuwingen = lees_uzk_lijst(bron)
    zonder = [r for r in regels if not r.loonschaal]
    assert [r.naam for r in zonder] == ["Julian de Olde"]
    assert any("zonder loonschaal" in w for w in waarschuwingen)


def test_dubbele_spaties_in_namen_worden_genormaliseerd():
    bron = _bestand([("1", "Marius  Girtoi ", "2026-02-01", "B2 Flex")])
    regels, _ = lees_uzk_lijst(bron)
    assert regels[0].naam == "Marius Girtoi"


def test_bestand_zonder_medewerkerkolom_wordt_geweigerd():
    wb = Workbook()
    wb.active.append(["Naam", "Waarde"])
    buffer = BytesIO()
    wb.save(buffer)
    with pytest.raises(ValueError, match="Medewerker"):
        lees_uzk_lijst(buffer.getvalue())
