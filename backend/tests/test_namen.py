"""Tests voor het koppelen van namen tussen Nitea, SNOOP en de lijst.

Aanleiding: in de bewaarde weken stonden vijf uitzendkrachten met uren maar
zonder bedrag, terwijl SNOOP hun loonschaal wel had. Hun naam stond in Nitea
anders geschreven dan in de uitzendkrachtenlijst, en de koppeling werkte alleen
op een exact gelijke naam.
"""

from datetime import date, time
from decimal import Decimal

import pytest

from app.services.namen import beste_match, delen, past
from app.services.calc.types import RegistratieRegel
from app.services.ingest import NiteaMedewerker, SnoopMedewerker
from app.services.seed.cao_glastuinbouw import cao_toeslag_regels
from app.services.tarief import CAT_100, LEVEL_ONE, SchaalTarief, TariefKaart
from app.services.verwerking import verwerk_week

KAART = TariefKaart(
    "L1", date(2026, 1, 1), None,
    {"B2F": SchaalTarief("B2F", {CAT_100: Decimal("28.94")})},
)
MA = date(2026, 6, 22)

# Zoals ze werkelijk in Nitea tegenover de lijst stonden.
ECHTE_GEVALLEN = [
    ("Cristian Bogdan Demian", "Christian Bogdan Demian"),  # spelling
    ("Visile Andrei Tiron", "Vasile Andrei Tiron"),  # typefout
    ("Robert Ionut Grasu", "Ionut Robert Grasu"),  # volgorde
    ("Elena Grasu", "Raluca Elena Grasu"),  # naamdeel weggelaten
    ("Isabela Doicsar", "Isabela Victoria Doicsar"),  # naamdeel weggelaten
]
# Bij Sterk Werk werken drie mensen Grasu; die mogen niet door elkaar lopen.
ALLE_NAMEN = [
    "Christian Bogdan Demian", "Vasile Andrei Tiron", "Ionut Robert Grasu",
    "Raluca Elena Grasu", "Malina Grasu", "Isabela Victoria Doicsar",
]


@pytest.mark.parametrize("nitea,lijst", ECHTE_GEVALLEN)
def test_afwijkende_spelling_koppelt_aan_de_juiste_persoon(nitea, lijst):
    assert beste_match(nitea, ALLE_NAMEN) == lijst


def test_andere_achternaam_koppelt_niet():
    assert beste_match("Marius Mic", ALLE_NAMEN) is None
    assert beste_match("Monika Gruca", ["Malina Grasu"]) is None


def test_naamgenoten_zonder_onderscheid_koppelen_niet():
    """Twee kandidaten met dezelfde score: niet gokken, want een verkeerde
    koppeling levert stilzwijgend een verkeerd tarief op."""
    assert beste_match("Grasu", ["Malina Grasu", "Raluca Elena Grasu"]) is None


def test_delen_en_past():
    assert delen("K.P. Sliwa (Kamil)") == ("sliwa", {"k", "p", "kamil"})
    assert past("Kamil Sliwa", "K.P. Sliwa (Kamil)") > past("Kamil Sliwa", "P. Sliwa")


def _nitea(naam):
    return NiteaMedewerker(naam=naam, nitea_id="1", registratie=[
        RegistratieRegel(MA, time(7, 0), time(15, 0), 480, 0)
    ])


def test_week_koppelt_de_snoop_regel_met_andere_spelling():
    snoop = [SnoopMedewerker(naam="Christian Bogdan Demian", loonschaal="B2 Flex", planning=[])]
    verwerking = verwerk_week(
        "L1", 2026, 26, snoop, [_nitea("Cristian Bogdan Demian")],
        cao_toeslag_regels(), KAART, LEVEL_ONE,
    )
    medewerker = verwerking.medewerkers[0]
    assert medewerker.loonschaal == "B2 Flex"
    assert medewerker.bedrag.totaal == Decimal("231.52")
    melding = next(m for m in verwerking.meldingen if "gekoppeld aan" in m)
    assert "Christian Bogdan Demian" in melding and "Controleer" in melding


def test_week_valt_terug_op_de_lijst_met_andere_spelling():
    verwerking = verwerk_week(
        "L1", 2026, 26, [], [_nitea("Robert Ionut Grasu")],
        cao_toeslag_regels(), KAART, LEVEL_ONE,
        bekende_loonschalen={"ionut robert grasu": "B2 Flex"},
    )
    assert verwerking.medewerkers[0].bedrag.totaal == Decimal("231.52")
    assert any("overgenomen van" in m for m in verwerking.meldingen)


def test_exacte_naam_levert_geen_koppelmelding_op():
    snoop = [SnoopMedewerker(naam="Marius Mic", loonschaal="B2 Flex", planning=[])]
    verwerking = verwerk_week(
        "L1", 2026, 26, snoop, [_nitea("Marius Mic")],
        cao_toeslag_regels(), KAART, LEVEL_ONE,
    )
    assert verwerking.meldingen == []


def test_alleen_dezelfde_achternaam_is_niet_genoeg():
    """"Jan Bakker" en "Piet Bakker" zijn twee mensen. Zonder deze grens zou de
    een stilzwijgend de loonschaal van de ander krijgen."""
    assert beste_match("Jan Bakker", ["Piet Bakker"]) is None
    assert beste_match("Jan Bakker", ["J. Bakker"]) == "J. Bakker"  # initiaal telt wel
    assert beste_match("Jan Bakker", ["Jan Bakker jr"]) is None  # andere achternaam


def test_week_neemt_geen_schaal_over_van_een_naamgenoot():
    verwerking = verwerk_week(
        "L1", 2026, 26, [], [_nitea("Jan Bakker")],
        cao_toeslag_regels(), KAART, LEVEL_ONE,
        bekende_loonschalen={"piet bakker": "B2 Flex"},
    )
    assert verwerking.medewerkers[0].loonschaal is None
    assert verwerking.medewerkers[0].bedrag.totaal == Decimal("0")
    assert not any("overgenomen van" in m for m in verwerking.meldingen)
