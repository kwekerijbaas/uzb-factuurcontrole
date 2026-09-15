"""Tests voor het inladen van een jaarlijst met alle bureaus door elkaar.

Aanleiding: de lijst over een heel jaar (8539 regels, 322 namen) werd in zijn
geheel geweigerd omdat achttien namen niet te plaatsen waren -- zeventien van
uitzendplatform Temper en één van "Kordaat", de korte schrijfwijze van
Cervokordaat. Daardoor kwamen ook de driehonderd namen die wél klopten niet
binnen, en bleven hun tarieven leeg.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.db import SessionLocal
from app.main import app
from app.services.opslag import bewaar_factoren, bewaar_loontabel, borg_uzb
from app.services.tarief.kaart import Loontabel, TariefFactor

KOP = [
    "Registratienummer", "Medewerker", "Datum", "Starttijd", "Eindtijd",
    "Werkelijke starttijd", "Werkelijke eindtijd", "Gewerkte uren", "Locatie",
    "Werkgever op datum shift", "Type uitzendkracht", "Tarief uitzendbureau",
]


def _lijst(regels: list[tuple[str, str, str]]) -> bytes:
    """(naam, werkgever, schaal) -> SNOOP-jaarlijst."""
    wb = Workbook()
    ws = wb.active
    ws.append(KOP)
    for naam, werkgever, schaal in regels:
        ws.append([
            "1", naam, "2026-05-10 00:00:00", "07:00", "15:30", None, None,
            8.0, "EW4", werkgever, "Uitzendkracht", schaal,
        ])
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, headers={"accept": "text/html"})


@pytest.fixture
def kaart_l1():
    """Een minimale Level One-kaart: B2 Flex heeft een tarief, kaal B2 niet."""
    with SessionLocal() as sessie:
        bewaar_loontabel(
            sessie, Loontabel("test", date(2026, 1, 1), {"B2": Decimal("14.99")})
        )
        uzb = borg_uzb(sessie, "L1", "Level One")
        bewaar_factoren(
            sessie,
            uzb,
            [TariefFactor("B2F", "B2", "100", Decimal("1.96"))],
            date(2026, 1, 1),
        )
        sessie.commit()
    yield


def test_jaarlijst_gaat_door_ondanks_namen_die_niet_te_plaatsen_zijn(client):
    """Zeventien Temper-namen mogen de driehonderd andere niet tegenhouden."""
    antwoord = client.post(
        "/uzk/lijst",
        files={"bestand": ("jaar.xlsx", _lijst([
            ("Ingeladen Kracht", "Level One", "B2 Flex"),
            ("Temper Kracht", "Temper", "Temper 2026"),
        ]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert antwoord.status_code == 200
    tekst = antwoord.text
    assert "Ingeladen Kracht" not in tekst  # gewoon ingeladen, geen aandachtspunt
    assert "Niet ingeladen (1)" in tekst
    assert "Temper Kracht" in tekst
    assert "niet in de app ingericht" in tekst


def test_kordaat_wordt_als_cervokordaat_ingeladen(client):
    """'Kordaat' is hoe SNOOP Cervokordaat schrijft; de schalen staan daar
    zonder achtervoegsel ('C4') en passen op de CK-kaart."""
    antwoord = client.post(
        "/uzk/lijst",
        files={"bestand": ("jaar.xlsx", _lijst([("Waldemar W", "Kordaat", "C4")]),
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert antwoord.status_code == 200
    assert "Cervokordaat: 1 uitzendkrachten" in antwoord.text
    assert "Niet ingeladen" not in antwoord.text


def test_schaal_zonder_tarief_op_de_kaart_wordt_gemeld(client, kaart_l1):
    """'B2' bij Level One levert geen tarief op: B2 Flex, B2 Vast en B2
    Seizoen hebben elk een ander tarief, dus het achtervoegsel is nodig."""
    antwoord = client.post(
        "/uzk/lijst",
        files={"bestand": ("jaar.xlsx", _lijst([
            ("Kale Schaal", "Level One", "B2"),
            ("Volledige Schaal", "Level One", "B2 Flex"),
        ]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert antwoord.status_code == 200
    tekst = antwoord.text
    assert "Wel ingeladen, maar zonder tarief (1)" in tekst
    assert "Kale Schaal" in tekst
    assert "Volledige Schaal" not in tekst
