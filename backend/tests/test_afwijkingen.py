"""Tests voor de tolerantiegrenzen en de terugval op een bekende loonschaal.

Aanleiding: op vier weken output stonden 514 afwijkingen, waarvan het grootste
deel geen echte fout was. `registratie_inconsistent` bestond voor 96% uit
verschillen van hooguit een kwartier -- Nitea's afronding. Daarnaast kregen
medewerkers die wel in Nitea maar niet in SNOOP staan geen tarief, waardoor hun
uren wel meetelden maar hun bedrag nul was en het weekgemiddelde bij Sterk Werk
op EUR 20,66 uitkwam in plaats van ongeveer EUR 30.
"""

from datetime import date, time
from decimal import Decimal

import pytest

from app.services.calc import (
    PlanningRegel,
    RegistratieRegel,
    WeekParameters,
    bereken_week,
)
from app.services.calc.types import (
    SOORT_REGISTRATIE_INCONSISTENT,
    SOORT_TIJD_VERSCHIL,
    SOORT_UREN_VERSCHIL,
)
from app.services.ingest import NiteaMedewerker, SnoopMedewerker
from app.services.seed.cao_glastuinbouw import cao_toeslag_regels
from app.services.tarief import CAT_100, LEVEL_ONE, SchaalTarief, TariefKaart
from app.services.verwerking import verwerk_week

REGELS = cao_toeslag_regels()
MA = date(2025, 9, 1)

KAART = TariefKaart(
    "L1", date(2026, 1, 1), None,
    {"B2F": SchaalTarief("B2F", {CAT_100: Decimal("28.94")})},
)


def _soorten(registratie, planning=(), params=None):
    resultaat = bereken_week(
        list(registratie), list(planning), REGELS, frozenset(), params or WeekParameters()
    )
    return {a.soort for a in resultaat.afwijkingen}


def test_kwartierafronding_van_nitea_geeft_geen_melding():
    """06:57-15:02 is 485 min; min 60 pauze is 425, maar Nitea meldt 420.
    Dat verschil van 5 minuten stond op bijna elke dag in de lijst."""
    regel = RegistratieRegel(MA, time(6, 57), time(15, 2), gewerkte_minuten=420, pauze_minuten=60)
    assert SOORT_REGISTRATIE_INCONSISTENT not in _soorten([regel])


def test_groot_onverklaard_verschil_wordt_wel_gemeld():
    """Een split shift laat een gat van uren achter; dat is geen afronding."""
    regel = RegistratieRegel(MA, time(7, 58), time(20, 34), gewerkte_minuten=345, pauze_minuten=60)
    assert SOORT_REGISTRATIE_INCONSISTENT in _soorten([regel])


@pytest.mark.parametrize(
    "gewerkt,verwacht_melding",
    [(475, False), (465, False), (420, True)],  # 5 en 15 min binnen, 60 min erbuiten
)
def test_urenverschil_pas_boven_een_kwartier(gewerkt, verwacht_melding):
    registratie = [RegistratieRegel(MA, time(7, 0), time(15, 0), gewerkt, 0)]
    planning = [PlanningRegel(MA, time(7, 0), time(15, 0), 480)]
    gemeld = SOORT_UREN_VERSCHIL in _soorten(registratie, planning)
    assert gemeld is verwacht_melding


def test_paar_minuten_vroeger_inklokken_is_geen_afwijking():
    registratie = [RegistratieRegel(MA, time(6, 57), time(15, 2), 485, 0)]
    planning = [PlanningRegel(MA, time(7, 0), time(15, 0), 485)]
    assert SOORT_TIJD_VERSCHIL not in _soorten(registratie, planning)


def test_uur_later_beginnen_is_wel_een_afwijking():
    registratie = [RegistratieRegel(MA, time(8, 0), time(16, 0), 480, 0)]
    planning = [PlanningRegel(MA, time(7, 0), time(15, 0), 480)]
    assert SOORT_TIJD_VERSCHIL in _soorten(registratie, planning)


def test_tolerantie_is_instelbaar():
    """Wie strenger wil controleren, zet de grens terug op nul."""
    registratie = [RegistratieRegel(MA, time(6, 57), time(15, 2), 485, 0)]
    planning = [PlanningRegel(MA, time(7, 0), time(15, 0), 485)]
    streng = WeekParameters(tolerantie_tijd_minuten=0)
    assert SOORT_TIJD_VERSCHIL in _soorten(registratie, planning, streng)


# --------------------------------------------------------------------------- #
# Terugval op een bekende loonschaal
# --------------------------------------------------------------------------- #
def _nitea(naam):
    return NiteaMedewerker(
        naam=naam, nitea_id="1",
        registratie=[RegistratieRegel(MA, time(7, 0), time(15, 0), 480, 0)],
    )


def test_zonder_snoop_wordt_de_bekende_loonschaal_gebruikt():
    verwerking = verwerk_week(
        "L1", 2026, 26, [], [_nitea("Elena Grasu")],
        REGELS, KAART, LEVEL_ONE,
        bekende_loonschalen={"elena grasu": "B2 Flex"},
    )
    medewerker = verwerking.medewerkers[0]
    assert medewerker.loonschaal == "B2 Flex"
    assert medewerker.bedrag.totaal == Decimal("231.52")  # 8 x 28,94
    assert any("overgenomen uit een eerdere week" in m for m in verwerking.meldingen)


def test_snoop_wint_van_de_onthouden_schaal():
    """Een actuele planning gaat voor; de schaal kan tussentijds gewijzigd zijn."""
    snoop = [SnoopMedewerker(naam="Elena Grasu", loonschaal="B2 Flex", planning=[])]
    verwerking = verwerk_week(
        "L1", 2026, 26, snoop, [_nitea("Elena Grasu")],
        REGELS, KAART, LEVEL_ONE,
        bekende_loonschalen={"elena grasu": "E5 Flex"},
    )
    assert verwerking.medewerkers[0].loonschaal == "B2 Flex"


def test_onbekende_medewerker_wordt_expliciet_gemeld():
    verwerking = verwerk_week(
        "L1", 2026, 26, [], [_nitea("Nieuwe Kracht")], REGELS, KAART, LEVEL_ONE
    )
    medewerker = verwerking.medewerkers[0]
    assert medewerker.bedrag.totaal == Decimal("0")
    assert medewerker.netto_uren == Decimal("8.00")  # uren blijven zichtbaar
    assert any("geen loonschaal bekend" in m for m in verwerking.meldingen)
