"""Een week verwerken: bronbestanden in, urenoverzicht per medewerker uit.

Bindt de losse onderdelen aaneen: ingest (SNOOP + Nitea) -> calc-engine ->
tariefmapping -> bedrag. Bewust vrij van database en HTTP, zodat het geheel als
functie te testen is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.services.calc import WeekParameters, bereken_week
from app.services.calc.types import Afwijking, WeekResultaat
from app.services.ingest import NiteaMedewerker, SnoopMedewerker
from app.services.tarief import (
    BedragResultaat,
    Kaartreeks,
    TariefKaart,
    UzbConventies,
    bereken_bedrag,
)


def normaliseer_naam(naam: str) -> str:
    """Namen uit SNOOP en Nitea verschillen in dubbele spaties en hoofdletters."""
    return re.sub(r"\s+", " ", str(naam or "")).strip().lower()


@dataclass
class MedewerkerResultaat:
    naam: str
    nitea_id: str | None
    loonschaal: str | None
    kaartcode: str | None
    resultaat: WeekResultaat
    bedrag: BedragResultaat
    afwijkingen: list[Afwijking] = field(default_factory=list)

    @property
    def netto_uren(self) -> Decimal:
        return self.resultaat.netto_uren


@dataclass
class WeekVerwerking:
    uzb_sleutel: str
    iso_jaar: int
    iso_week: int
    medewerkers: list[MedewerkerResultaat] = field(default_factory=list)
    meldingen: list[str] = field(default_factory=list)

    @property
    def totaal_uren(self) -> Decimal:
        return sum((m.netto_uren for m in self.medewerkers), Decimal("0"))

    @property
    def totaal_bedrag(self) -> Decimal:
        return sum((m.bedrag.totaal for m in self.medewerkers), Decimal("0"))


def ontbrekende_loonschalen(verwerking: WeekVerwerking) -> list[str]:
    """Wie in deze week geen loonschaal heeft, op naam.

    Zonder loonschaal is er geen tarief en dus geen bedrag. Zo iemand telt wel
    mee in de uren, waardoor het overzicht compleet lijkt terwijl het totaal te
    laag is -- en dat is precies wat er tegen een factuur naast wordt gelegd.
    Daarom wordt een week met een ontbrekende schaal niet verwerkt.
    """
    return sorted(m.naam for m in verwerking.medewerkers if not m.loonschaal)


def verwerk_week(
    uzb_sleutel: str,
    iso_jaar: int,
    iso_week: int,
    snoop: list[SnoopMedewerker],
    nitea: list[NiteaMedewerker],
    toeslag_regels: list,
    kaart: TariefKaart | Kaartreeks | None,
    conventies: UzbConventies,
    feestdagen: frozenset[date] = frozenset(),
    parameters: WeekParameters | None = None,
    bekende_loonschalen: dict[str, str] | None = None,
    handmatige_loonschalen: dict[str, str] | None = None,
) -> WeekVerwerking:
    """Bereken voor elke geregistreerde medewerker de uren en het bedrag.

    Nitea is leidend voor wie er gewerkt heeft; SNOOP levert alleen de
    loonschaal. Wie wel gepland maar niet geregistreerd is, heeft simpelweg niet
    gewerkt en komt niet in het overzicht. Verschillen tussen planning en
    registratie worden niet gemeld: Nitea wordt vóór het verwerken al
    gecontroleerd, dus die zeggen niets over de te factureren uren.

    Staat iemand niet in SNOOP, dan wordt teruggevallen op zijn laatst bekende
    loonschaal (`bekende_loonschalen`). Zonder die terugval zouden de gewerkte
    uren wel meetellen maar het bedrag nul zijn, waardoor het weekgemiddelde
    stilzwijgend te laag uitkomt.

    Een **handmatig** ingevulde schaal (`handmatige_loonschalen`) wint juist van
    SNOOP: die is ingevuld omdat het bestand het fout of niet had. Wijkt SNOOP
    af, dan komt daar een melding van, zodat een schaalwijziging bij het bureau
    niet ongemerkt blijft hangen achter een oude handmatige waarde.
    """
    verwerking = WeekVerwerking(uzb_sleutel, iso_jaar, iso_week)
    reeks = kaart if isinstance(kaart, Kaartreeks) else Kaartreeks.van_kaart(kaart)
    snoop_op_naam = {normaliseer_naam(s.naam): s for s in snoop}
    gezien: set[str] = set()

    for medewerker in nitea:
        sleutel = normaliseer_naam(medewerker.naam)
        gezien.add(sleutel)
        planning_bron = snoop_op_naam.get(sleutel)

        resultaat = bereken_week(
            medewerker.registratie,
            planning_bron.planning if planning_bron else [],
            toeslag_regels,
            feestdagen,
            parameters or WeekParameters(),
        )

        snoop_schaal = planning_bron.loonschaal if planning_bron else None
        handmatig = (handmatige_loonschalen or {}).get(sleutel)
        loonschaal = handmatig or snoop_schaal
        if not loonschaal and bekende_loonschalen:
            loonschaal = bekende_loonschalen.get(sleutel)
        if handmatig and snoop_schaal and snoop_schaal != handmatig:
            verwerking.meldingen.append(
                f"{medewerker.naam}: SNOOP noemt loonschaal '{snoop_schaal}', "
                f"maar handmatig is '{handmatig}' ingesteld -- de week is met "
                f"'{handmatig}' gerekend. Klopt de SNOOP-waarde, neem die dan "
                "over bij Uitzendkrachten."
            )
        kaartcode = conventies.kaartcode(loonschaal)
        schalen = reeks.schalen_van(kaartcode)
        heeft_tarief = any(s is not None for _, s in schalen.periodes)
        if loonschaal and not heeft_tarief and not reeks.is_leeg:
            verwerking.meldingen.append(
                f"{medewerker.naam}: geen tarief voor loonschaal "
                f"'{loonschaal}' (kaartcode {kaartcode}) -- geen bedrag berekend"
            )
        elif not loonschaal:
            verwerking.meldingen.append(
                f"{medewerker.naam}: geen loonschaal bekend -- "
                f"{resultaat.netto_uren} uur zonder bedrag"
            )

        verwerking.medewerkers.append(
            MedewerkerResultaat(
                naam=medewerker.naam,
                nitea_id=medewerker.nitea_id,
                loonschaal=loonschaal,
                kaartcode=kaartcode,
                resultaat=resultaat,
                bedrag=bereken_bedrag(resultaat, schalen, conventies),
                afwijkingen=resultaat.afwijkingen,
            )
        )

    verwerking.medewerkers.sort(key=lambda m: m.naam)
    return verwerking
