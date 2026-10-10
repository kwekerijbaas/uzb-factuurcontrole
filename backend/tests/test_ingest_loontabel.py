"""Tests voor het inlezen van een geüploade CAO-loontabel (SPEC §6)."""

from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook

from app.services.ingest import lees_loontabel


def maak_xlsx(rijen, kop=("Schaal", "Uurloon")) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(list(kop))
    for rij in rijen:
        ws.append(list(rij))
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_leest_schaal_en_uurloon():
    bron = maak_xlsx([("B2", 15.0), ("B4", 16.25), ("C2", 17.4)])
    tabel, waarschuwingen = lees_loontabel(bron, "CAO 2026-07", date(2026, 7, 1))

    assert waarschuwingen == []
    assert tabel.ingangsdatum == date(2026, 7, 1)
    assert tabel.loon("B2") == Decimal("15.0")
    assert tabel.loon("C2") == Decimal("17.4")


def test_herkent_alternatieve_headers_en_voorloopregels():
    """Een CAO-export begint vaak met een titelregel boven de kolomkoppen."""
    wb = Workbook()
    ws = wb.active
    ws.append(["Loontabel glastuinbouw per 1 juli 2026"])
    ws.append([])
    ws.append(["Functiegroep", "Omschrijving", "Loon"])
    ws.append(["B2", "Medewerker", "€ 15,00"])
    buffer = BytesIO()
    wb.save(buffer)

    tabel, _ = lees_loontabel(buffer.getvalue(), "CAO", date(2026, 7, 1))
    assert tabel.loon("B2") == Decimal("15.00")  # NL-notatie + euroteken


def test_dubbele_schaal_met_afwijkend_loon_waarschuwt():
    bron = maak_xlsx([("B2", 15.0), ("B2", 15.5)])
    tabel, waarschuwingen = lees_loontabel(bron, "CAO", date(2026, 7, 1))
    assert tabel.loon("B2") == Decimal("15.0")  # eerste aangehouden
    assert any("B2" in w for w in waarschuwingen)


def test_bestand_zonder_loonkolom_wordt_geweigerd():
    bron = maak_xlsx([("B2", "x")], kop=("Naam", "Waarde"))
    with pytest.raises(ValueError, match="geen loonregels"):
        lees_loontabel(bron, "CAO", date(2026, 7, 1))
