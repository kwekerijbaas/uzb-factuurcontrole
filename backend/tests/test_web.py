"""Tests voor de toegangscontrole, verwerking en export.

De databaseloze onderdelen worden hier gedekt; de volledige flow (upload ->
overzicht) is end-to-end tegen Postgres gedraaid.
"""

from __future__ import annotations

import base64
import io
import json
from datetime import date, time
from decimal import Decimal

import openpyxl
import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.requests import Request

from app.auth import huidige_gebruiker
from app.config import settings
from app.services.calc.types import PlanningRegel, RegistratieRegel
from app.services.export import bestandsnaam, bouw_overzicht
from app.services.ingest import NiteaMedewerker, SnoopMedewerker
from app.services.seed.cao_glastuinbouw import cao_toeslag_regels
from app.services.tarief import LEVEL_ONE, CAT_100, CAT_150, SchaalTarief, TariefKaart
from app.services.verwerking import normaliseer_naam, verwerk_week

MA = date(2025, 9, 1)


def _request(headers: dict[str, str] | None = None) -> Request:
    rauw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": Headers(raw=rauw).raw, "method": "GET", "path": "/"})


# --------------------------------------------------------------------------- #
# Toegang
# --------------------------------------------------------------------------- #
def test_gebruiker_uit_easy_auth_headers():
    principal = base64.b64encode(
        json.dumps({"claims": [{"typ": "roles", "val": "beheerder"}]}).encode()
    ).decode()
    gebruiker = huidige_gebruiker(
        _request(
            {
                "x-ms-client-principal-name": "ola@kwekerijbaas.nl",
                "x-ms-client-principal-id": "abc-123",
                "x-ms-client-principal": principal,
            }
        )
    )
    assert gebruiker.naam == "ola@kwekerijbaas.nl"
    assert gebruiker.rollen == ("beheerder",)
    assert not gebruiker.is_ontwikkelaar


def test_zonder_login_geen_toegang(monkeypatch):
    """In Azure mag de app nooit zonder ingelogde gebruiker bereikbaar zijn."""
    monkeypatch.setattr(settings, "auth_vereist", True)
    with pytest.raises(HTTPException) as fout:
        huidige_gebruiker(_request())
    assert fout.value.status_code == 401


def test_lokaal_zonder_login_toegestaan(monkeypatch):
    monkeypatch.setattr(settings, "auth_vereist", False)
    assert huidige_gebruiker(_request()).is_ontwikkelaar


def test_onleesbare_principal_levert_geen_rollen():
    gebruiker = huidige_gebruiker(
        _request(
            {"x-ms-client-principal-name": "ola@kwekerijbaas.nl", "x-ms-client-principal": "!!"}
        )
    )
    assert gebruiker.rollen == ()


# --------------------------------------------------------------------------- #
# Verwerking
# --------------------------------------------------------------------------- #
KAART = TariefKaart(
    "L1",
    date(2026, 1, 1),
    None,
    {"B2F": SchaalTarief("B2F", {CAT_100: Decimal("28.94"), CAT_150: Decimal("33.92")})},
)


def _nitea(naam: str, uren: int = 8) -> NiteaMedewerker:
    return NiteaMedewerker(
        naam=naam,
        nitea_id="1",
        registratie=[RegistratieRegel(MA, time(7, 0), time(15, 0), uren * 60, 0)],
    )


def _snoop(naam: str, schaal: str | None = "B2 Flex") -> SnoopMedewerker:
    return SnoopMedewerker(
        naam=naam,
        loonschaal=schaal,
        planning=[PlanningRegel(MA, time(7, 0), time(15, 0), 480)],
    )


@pytest.mark.parametrize(
    "links,rechts",
    [("Marius  Mic", "Marius Mic"), ("ALEXANDRA REBEGA", "Alexandra Rebega")],
)
def test_namen_uit_snoop_en_nitea_matchen(links, rechts):
    """SNOOP en Nitea verschillen in dubbele spaties en hoofdletters."""
    assert normaliseer_naam(links) == normaliseer_naam(rechts)


def test_verwerking_koppelt_uren_en_bedrag():
    verwerking = verwerk_week(
        "L1", 2026, 25, [_snoop("Marius  Mic")], [_nitea("Marius Mic")],
        cao_toeslag_regels(), KAART, LEVEL_ONE,
    )
    assert len(verwerking.medewerkers) == 1
    medewerker = verwerking.medewerkers[0]
    assert medewerker.kaartcode == "B2F"
    assert medewerker.netto_uren == Decimal("8.00")
    assert medewerker.bedrag.totaal == Decimal("231.52")  # 8 x 28,94
    assert verwerking.meldingen == []


def test_registratie_zonder_snoop_wordt_gemeld_maar_wel_geteld():
    verwerking = verwerk_week(
        "L1", 2026, 25, [], [_nitea("Onbekende Kracht")],
        cao_toeslag_regels(), KAART, LEVEL_ONE,
    )
    assert verwerking.medewerkers[0].netto_uren == Decimal("8.00")
    assert any("niet gevonden in SNOOP" in m for m in verwerking.meldingen)


def test_planning_zonder_registratie_geeft_geen_regel_met_nul_uren():
    verwerking = verwerk_week(
        "L1", 2026, 25, [_snoop("Niet Verschenen")], [],
        cao_toeslag_regels(), KAART, LEVEL_ONE,
    )
    assert verwerking.medewerkers == []
    assert any("geen registratie in Nitea" in m for m in verwerking.meldingen)


def test_onbekende_loonschaal_levert_melding_en_geen_bedrag():
    verwerking = verwerk_week(
        "L1", 2026, 25, [_snoop("Marius Mic", "Z9 Flex")], [_nitea("Marius Mic")],
        cao_toeslag_regels(), KAART, LEVEL_ONE,
    )
    assert verwerking.medewerkers[0].bedrag.totaal == Decimal("0")
    assert any("geen tarief" in m for m in verwerking.meldingen)


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def test_overzicht_opent_op_totaal_week_en_bevat_de_totalen():
    verwerking = verwerk_week(
        "L1", 2026, 25, [_snoop("Marius Mic")], [_nitea("Marius Mic")],
        cao_toeslag_regels(), KAART, LEVEL_ONE,
    )
    wb = openpyxl.load_workbook(io.BytesIO(bouw_overzicht(verwerking, "Level One", KAART)))

    assert wb.sheetnames == ["Totaal week", "Per dag", "Tarieven", "Afwijkingen"]
    assert wb.active.title == "Totaal week"

    ws = wb["Totaal week"]
    rijen = [r for r in ws.iter_rows(min_row=4, values_only=True) if r[0]]
    assert rijen[0][0] == "Marius Mic"
    assert rijen[-1][0] == "TOTAAL"
    assert rijen[-1][-1] == pytest.approx(231.52)


def test_meldingen_belanden_op_het_afwijkingen_tabblad():
    verwerking = verwerk_week(
        "L1", 2026, 25, [], [_nitea("Onbekende Kracht")],
        cao_toeslag_regels(), KAART, LEVEL_ONE,
    )
    wb = openpyxl.load_workbook(io.BytesIO(bouw_overzicht(verwerking, "Level One", KAART)))
    tekst = "\n".join(
        str(c.value) for rij in wb["Afwijkingen"].iter_rows() for c in rij if c.value
    )
    assert "niet gevonden in SNOOP" in tekst


def test_bestandsnaam_is_veilig():
    verwerking = verwerk_week("L1", 2026, 25, [], [], cao_toeslag_regels(), None, LEVEL_ONE)
    assert bestandsnaam("Level One", verwerking) == "UZB-overzicht_Level_One_week_25_2026.xlsx"
