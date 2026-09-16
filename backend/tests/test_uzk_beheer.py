"""Tests voor de handmatige loonschaal en het afleiden van de week.

Aanleiding: een handmatig gecorrigeerde schaal werd bij de volgende upload
stilzwijgend overschreven, en een verkeerd getypt weeknummer zette de week
onder het verkeerde nummer vast -- zonder manier om die weer weg te halen.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.services.calc.types import PlanningRegel, RegistratieRegel
from app.services.ingest import NiteaMedewerker, SnoopMedewerker
from app.services.seed.cao_glastuinbouw import cao_toeslag_regels
from app.services.tarief import LEVEL_ONE, CAT_100, SchaalTarief, TariefKaart
from app.services.verwerking import verwerk_week

KAART = TariefKaart(
    "L1", date(2026, 1, 1), None,
    {
        "B2F": SchaalTarief("B2F", {CAT_100: Decimal("28.94")}),
        "C2F": SchaalTarief("C2F", {CAT_100: Decimal("29.95")}),
    },
)
MA = date(2026, 6, 22)  # maandag week 26


def _nitea(naam, datum=MA):
    return NiteaMedewerker(naam=naam, nitea_id="1", registratie=[
        RegistratieRegel(datum, time(7, 0), time(15, 0), 480, 0)
    ])


def _snoop(naam, schaal="B2 Flex", datum=MA):
    return SnoopMedewerker(naam=naam, loonschaal=schaal, planning=[
        PlanningRegel(datum, time(7, 0), time(15, 0), 480)
    ])


# --------------------------------------------------------------------------- #
# Handmatige schaal wint van SNOOP, met melding
# --------------------------------------------------------------------------- #
def test_handmatige_schaal_wint_van_snoop_met_melding():
    """De schaal is juist met de hand ingevuld omdat het bestand het fout had;
    het bestand mag die correctie niet elke week opnieuw ongedaan maken."""
    verwerking = verwerk_week(
        "L1", 2026, 26, [_snoop("Marius Mic", "B2 Flex")], [_nitea("Marius Mic")],
        cao_toeslag_regels(), KAART, LEVEL_ONE,
        handmatige_loonschalen={"marius mic": "C2 Flex"},
    )
    medewerker = verwerking.medewerkers[0]
    assert medewerker.loonschaal == "C2 Flex"
    assert medewerker.bedrag.totaal == Decimal("239.60")  # 8 x 29,95
    assert any("SNOOP noemt" in m and "C2 Flex" in m for m in verwerking.meldingen)


def test_gelijke_waarden_geven_geen_melding():
    verwerking = verwerk_week(
        "L1", 2026, 26, [_snoop("Marius Mic", "B2 Flex")], [_nitea("Marius Mic")],
        cao_toeslag_regels(), KAART, LEVEL_ONE,
        handmatige_loonschalen={"marius mic": "B2 Flex"},
    )
    assert verwerking.meldingen == []


# --------------------------------------------------------------------------- #
# Week afleiden uit de bestanden
# --------------------------------------------------------------------------- #
def test_week_wordt_uit_de_bestanden_afgeleid():
    from app.routers.week import bepaal_week

    assert bepaal_week([_nitea("A")], [_snoop("A")]) == (2026, 26)


def test_bestanden_van_verschillende_weken_worden_geweigerd():
    """Precies de fout die dit moet voorkomen: SNOOP van week 25 bij het
    Nitea-overzicht van week 26 -- de week zou onder het verkeerde nummer
    bewaard worden."""
    from app.routers.week import bepaal_week

    with pytest.raises(HTTPException) as fout:
        bepaal_week([_nitea("A")], [_snoop("A", datum=date(2026, 6, 15))])
    assert "dezelfde week" in str(fout.value.detail)


def test_nitea_over_meerdere_weken_wordt_geweigerd():
    from app.routers.week import bepaal_week

    twee_weken = [_nitea("A"), _nitea("B", datum=date(2026, 6, 29))]
    with pytest.raises(HTTPException) as fout:
        bepaal_week(twee_weken, [])
    assert "één week" in str(fout.value.detail)


def test_zondag_van_dezelfde_iso_week_mag_wel():
    from app.routers.week import bepaal_week

    assert bepaal_week(
        [_nitea("A"), _nitea("B", datum=date(2026, 6, 28))], [_snoop("A")]
    ) == (2026, 26)


# --------------------------------------------------------------------------- #
# Wie handmatig invulde
# --------------------------------------------------------------------------- #
def test_zet_loonschaal_registreert_wie_en_wist_bij_overname():
    """De naam hoort bij de bescherming: hij wordt getoond bij de ja/nee-vraag
    zodra een upload over de handmatige waarde heen wil. Neemt iemand bewust de
    bestandswaarde over, dan vervalt de bescherming én de naam -- daarna mogen
    imports weer geruisloos overschrijven."""
    from app.services.opslag import zet_loonschaal

    class Rij:
        loonschaal_code = None
        schaal_handmatig = False
        schaal_door = None

    rij = Rij()
    zet_loonschaal(rij, "C2 Flex", handmatig=True, door="ola@kwekerijbaas.nl")
    assert rij.schaal_handmatig and rij.schaal_door == "ola@kwekerijbaas.nl"

    zet_loonschaal(rij, "B2 Flex", handmatig=False, door="tim@kwekerijbaas.nl")
    assert not rij.schaal_handmatig
    assert rij.schaal_door is None  # bescherming weg -> naam ook
