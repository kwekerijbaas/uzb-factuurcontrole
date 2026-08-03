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
    SchaalTarief,
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


def verwerk_week(
    uzb_sleutel: str,
    iso_jaar: int,
    iso_week: int,
    snoop: list[SnoopMedewerker],
    nitea: list[NiteaMedewerker],
    toeslag_regels: list,
    kaart: TariefKaart | None,
    conventies: UzbConventies,
    feestdagen: frozenset[date] = frozenset(),
    parameters: WeekParameters | None = None,
) -> WeekVerwerking:
    """Bereken voor elke geregistreerde medewerker de uren en het bedrag.

    Nitea is leidend voor wie er gewerkt heeft; SNOOP levert de planning en de
    loonschaal. Medewerkers die wél gepland maar niet geregistreerd zijn worden
    als melding teruggegeven, niet als regel met nul uren.
    """
    verwerking = WeekVerwerking(uzb_sleutel, iso_jaar, iso_week)
    snoop_op_naam = {normaliseer_naam(s.naam): s for s in snoop}
    gezien: set[str] = set()

    for medewerker in nitea:
        sleutel = normaliseer_naam(medewerker.naam)
        gezien.add(sleutel)
        planning_bron = snoop_op_naam.get(sleutel)
        if planning_bron is None:
            verwerking.meldingen.append(
                f"{medewerker.naam}: wel uren in Nitea, niet gevonden in SNOOP "
                "(geen planning en geen loonschaal)"
            )

        resultaat = bereken_week(
            medewerker.registratie,
            planning_bron.planning if planning_bron else [],
            toeslag_regels,
            feestdagen,
            parameters or WeekParameters(),
        )

        loonschaal = planning_bron.loonschaal if planning_bron else None
        kaartcode = conventies.kaartcode(loonschaal)
        schaal: SchaalTarief | None = kaart.schaal(kaartcode) if kaart else None
        if loonschaal and schaal is None:
            verwerking.meldingen.append(
                f"{medewerker.naam}: geen tarief voor loonschaal "
                f"'{loonschaal}' (kaartcode {kaartcode}) -- niet meegerekend"
            )

        verwerking.medewerkers.append(
            MedewerkerResultaat(
                naam=medewerker.naam,
                nitea_id=medewerker.nitea_id,
                loonschaal=loonschaal,
                kaartcode=kaartcode,
                resultaat=resultaat,
                bedrag=bereken_bedrag(resultaat, schaal, conventies),
                afwijkingen=resultaat.afwijkingen,
            )
        )

    for sleutel, bron in sorted(snoop_op_naam.items()):
        if sleutel not in gezien:
            verwerking.meldingen.append(
                f"{bron.naam}: wel ingepland in SNOOP, geen registratie in Nitea"
            )

    verwerking.medewerkers.sort(key=lambda m: m.naam)
    return verwerking
