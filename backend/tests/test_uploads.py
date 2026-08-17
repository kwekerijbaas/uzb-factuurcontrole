"""Tests voor de typecontrole op uploads en de foutafhandeling.

Aanleiding: een verkeerd gekozen bestand liet openpyxl of pdfplumber struikelen
(BadZipFile, PDFSyntaxError). Die fout werd nergens opgevangen en verscheen als
kale 'Internal Server Error' -- zonder te vertellen welk veld fout was.
"""

from __future__ import annotations

import io

import pytest
from fastapi import HTTPException, UploadFile

from app.config import settings
from app.uploads import EXCEL, PDF, lees_upload, leesfouten

XLSX = b"PK\x03\x04rest van een werkmap"
PDF_BYTES = b"%PDF-1.7\nrest van een document"
XLS_OUD = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1oud"


def _upload(inhoud: bytes, naam: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(inhoud), filename=naam)


async def _fout(inhoud: bytes, veld: str, soort) -> str:
    with pytest.raises(HTTPException) as fout:
        await lees_upload(_upload(inhoud, "bestand.xlsx"), veld, soort)
    assert fout.value.status_code == 400
    return str(fout.value.detail)


@pytest.mark.anyio
async def test_juiste_soort_komt_er_gewoon_door():
    assert await lees_upload(_upload(XLSX, "week.xlsx"), "SNOOP-export", EXCEL) == XLSX
    assert await lees_upload(_upload(PDF_BYTES, "week.pdf"), "Nitea", PDF) == PDF_BYTES


@pytest.mark.anyio
async def test_verwisselde_bestanden_worden_als_zodanig_gemeld():
    """De meest gemaakte fout: SNOOP en Nitea in elkaars veld."""
    assert "verwisseld" in await _fout(PDF_BYTES, "SNOOP-export", EXCEL)
    assert "verwisseld" in await _fout(XLSX, "Nitea-overzicht", PDF)


@pytest.mark.anyio
async def test_oud_excel_krijgt_een_bruikbare_aanwijzing():
    melding = await _fout(XLS_OUD, "SNOOP-export", EXCEL)
    assert ".xls" in melding and "xlsx" in melding


@pytest.mark.anyio
async def test_leeg_veld_en_onbekend_formaat():
    assert "geen bestand gekozen" in await _fout(b"", "SNOOP-export", EXCEL)
    assert "Excel-bestand" in await _fout(b"willekeurige bytes", "SNOOP-export", EXCEL)


@pytest.mark.anyio
async def test_veld_dat_twee_formaten_aanvaardt():
    """De loontabel komt als PDF van de cao-partijen, soms als Excel van ons."""
    soort = PDF | EXCEL
    assert await lees_upload(_upload(XLSX, "lonen.xlsx"), "loontabel", soort) == XLSX
    assert await lees_upload(_upload(PDF_BYTES, "lonen.pdf"), "loontabel", soort) == PDF_BYTES
    assert "PDF-bestand" in await _fout(b"iets anders", "loontabel", soort)


def test_leesfout_van_de_inlezer_blijft_zijn_eigen_tekst_houden():
    """Een ValueError uit een inlezer is al voor de gebruiker geschreven."""
    with pytest.raises(HTTPException) as fout:
        with leesfouten("SNOOP-export", "week.xlsx"):
            raise ValueError("de kolomkoppen zijn niet herkend")
    assert fout.value.status_code == 400
    assert "kolomkoppen" in str(fout.value.detail)


def test_onverwachte_leesfout_wordt_400_en_wordt_gelogd(caplog):
    """Een beschadigd bestand is een gebruikersfout, geen serverfout -- maar de
    traceback moet wel in het log staan om na te kunnen kijken."""
    with pytest.raises(HTTPException) as fout:
        with leesfouten("Nitea-overzicht", "week.pdf"):
            raise KeyError("onverwacht")
    assert fout.value.status_code == 400
    assert "KeyError" in str(fout.value.detail)
    assert any(r.exc_info for r in caplog.records)


# --------------------------------------------------------------------------- #
# Foutpagina's
# --------------------------------------------------------------------------- #
def _client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "auth_vereist", False)
    return TestClient(app, raise_server_exceptions=False)


BROWSER = {"accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


def test_browser_krijgt_een_leesbare_pagina_bij_een_bestandsfout(monkeypatch):
    antwoord = _client(monkeypatch).post(
        "/week/verwerk",
        data={"iso_jaar": 2026, "iso_week": 30},
        headers=BROWSER,
        files={"snoop_bestand": ("n.pdf", PDF_BYTES), "nitea_bestand": ("s.xlsx", XLSX)},
    )
    assert antwoord.status_code == 400
    assert "<html" in antwoord.text
    assert "verwisseld" in antwoord.text


def test_onverwachte_fout_geeft_pagina_met_kenmerk(monkeypatch, caplog):
    """Zonder kenmerk is een melding 'het werkt niet' niet terug te vinden."""
    from fastapi import APIRouter

    from app.main import app

    router = APIRouter()

    @router.get("/_test_kapot", include_in_schema=False)
    def kapot():
        raise RuntimeError("iets onverwachts")

    app.include_router(router)
    try:
        antwoord = _client(monkeypatch).get("/_test_kapot", headers=BROWSER)
        assert antwoord.status_code == 500
        assert "Kenmerk" in antwoord.text
        assert any("onverwachte fout" in r.message for r in caplog.records)
    finally:
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "path", "") != "/_test_kapot"
        ]


def test_niet_browsers_krijgen_nog_steeds_json(monkeypatch):
    antwoord = _client(monkeypatch).post(
        "/week/verwerk",
        data={"iso_jaar": 2026, "iso_week": 30},
        files={"snoop_bestand": ("n.pdf", PDF_BYTES), "nitea_bestand": ("s.xlsx", XLSX)},
    )
    assert antwoord.status_code == 400
    assert "verwisseld" in antwoord.json()["detail"]


def test_verwerkadres_in_de_adresbalk_leidt_terug_naar_het_formulier(monkeypatch):
    """/week/verwerk bestaat alleen als doel van het formulier. Komt de browser
    er met een GET langs -- adresbalk, geschiedenis, of een download die Edge
    opnieuw ophaalt -- dan gaf dat een doodlopende 'Method Not Allowed'."""
    client = _client(monkeypatch)
    for pad, formulier in [
        ("/week/verwerk", "/week"),
        ("/facturen/controleer", "/facturen"),
        ("/tarieven/tariefkaart", "/tarieven"),
        ("/uzk/lijst", "/uzk"),
    ]:
        antwoord = client.get(pad, headers=BROWSER, follow_redirects=False)
        assert antwoord.status_code == 303, pad
        assert antwoord.headers["location"] == formulier, pad


def test_onbekende_pagina_meldt_dat_in_het_nederlands(monkeypatch):
    antwoord = _client(monkeypatch).get("/bestaat-niet", headers=BROWSER)
    assert antwoord.status_code == 404
    assert "Deze pagina bestaat niet" in antwoord.text
    assert "Not Found" not in antwoord.text
