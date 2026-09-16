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
from app.services.namen import beste_match
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


def _bureaunaam(sleutel: str) -> str:
    from app.services.tarief.uzb import CONVENTIES

    conv = CONVENTIES.get(sleutel)
    return conv.naam if conv else sleutel


def _ander_bureau(loonschaal: str, uzb_sleutel: str) -> str | None:
    """Hoort deze loonschaal herkenbaar bij een ánder uitzendbureau?

    "D2 SW" bij iemand die onder Level One staat betekent bijna altijd dat de
    persoon onder het verkeerde bureau is beland -- dat gebeurde toen het
    Nitea-overzicht van een Level One-week ook Sterk Werk-krachten bevatte.
    Zonder deze melding blijft het een bedrag van nul zonder zichtbare oorzaak.
    """
    from app.services.ingest.herkenning import herken_uzb

    class _Regel:
        pass

    regel = _Regel()
    regel.loonschaal = loonschaal
    regel.werkgever = None
    ander, _ = herken_uzb([regel])
    if ander is None or ander == uzb_sleutel:
        return None
    # Level One en zijn jeugd-payroll delen hun schalen; dat is geen fout.
    if {ander, uzb_sleutel} <= {"L1", "L1_JEUGD"}:
        return None
    return _bureaunaam(ander)


@dataclass
class MedewerkerResultaat:
    naam: str
    nitea_id: str | None
    loonschaal: str | None
    kaartcode: str | None
    resultaat: WeekResultaat
    bedrag: BedragResultaat
    # De Nitea-regels zoals ze geregistreerd zijn; het tabblad 'Per dag' toont
    # daaruit begin, eind en pauze. De factuurcontrole verwijst naar juist die
    # kolommen, dus ze mogen niet leeg blijven.
    registratie: list = field(default_factory=list)
    afwijkingen: list[Afwijking] = field(default_factory=list)
    # Wordt door het bureau los gefactureerd (bv. techniek, apart geboekt):
    # krijgt een eigen overzicht en een eigen factuurcontrole.
    apart: bool = False
    # Waarom er geen tarief is, als dat meer is dan "staat niet op de kaart"
    # (bijvoorbeeld: de schaal hoort bij een ander uitzendbureau).
    tarief_reden: str | None = None

    @property
    def netto_uren(self) -> Decimal:
        return self.resultaat.netto_uren

    @property
    def heeft_tarief(self) -> bool:
        """Zijn de gewerkte uren op geld gezet?

        Onwaar als er wel uren zijn maar geen bedragregels: de loonschaal
        ontbreekt, of de schaal staat niet op de tariefkaart. Wie geen uren
        heeft, mist ook niets.
        """
        return bool(self.bedrag.regels) or not self.resultaat.netto_minuten

    @property
    def tarief_ontbreekt_omdat(self) -> str | None:
        """Korte reden waarom er geen bedrag is, voor in meldingen."""
        if self.heeft_tarief:
            return None
        if self.tarief_reden:
            return self.tarief_reden
        if not self.loonschaal:
            return "geen loonschaal"
        return f"loonschaal '{self.loonschaal}' staat niet op de tariefkaart"


@dataclass
class WeekVerwerking:
    uzb_sleutel: str
    iso_jaar: int
    iso_week: int
    medewerkers: list[MedewerkerResultaat] = field(default_factory=list)
    meldingen: list[str] = field(default_factory=list)
    # Gevuld bij een afgesplitst deel (één apart gefactureerde persoon).
    label: str | None = None

    @property
    def totaal_uren(self) -> Decimal:
        return sum((m.netto_uren for m in self.medewerkers), Decimal("0"))

    @property
    def totaal_bedrag(self) -> Decimal:
        return sum((m.bedrag.totaal for m in self.medewerkers), Decimal("0"))

    @property
    def zonder_tarief(self) -> list[MedewerkerResultaat]:
        """Wie wel uren maar geen bedrag heeft, op naam gesorteerd."""
        return sorted(
            (m for m in self.medewerkers if not m.heeft_tarief), key=lambda m: m.naam
        )

    @property
    def deels_zonder_tarief(self) -> list[MedewerkerResultaat]:
        """Wie wél een bedrag heeft, maar niet over al zijn uren.

        Ontbreekt op de kaart een tariefkolom (de jeugdkaart heeft bijvoorbeeld
        geen feestdagtarief), dan vielen die uren stil uit het bedrag: de
        persoon leek gewoon afgerekend terwijl zijn bedrag te laag was.
        """
        return sorted(
            (
                m
                for m in self.medewerkers
                if m.heeft_tarief and m.bedrag.ontbrekende_minuten
            ),
            key=lambda m: m.naam,
        )

    def gesplitst(self) -> tuple[WeekVerwerking, list[WeekVerwerking]]:
        """Splits de week in het hoofddeel en één deel per apart gefactureerde
        persoon.

        Wie apart gefactureerd wordt, staat op een eigen factuur; in het
        hoofdoverzicht zou hij het weektotaal vertekenen en bij de
        factuurcontrole als 'niet gefactureerd' opduiken. Meldingen over een
        persoon (ze beginnen met zijn naam) gaan mee naar zijn deel; de
        overige blijven bij het hoofddeel.
        """
        hoofd = WeekVerwerking(self.uzb_sleutel, self.iso_jaar, self.iso_week)
        delen: list[WeekVerwerking] = []
        for medewerker in self.medewerkers:
            if not medewerker.apart:
                hoofd.medewerkers.append(medewerker)
                continue
            deel = WeekVerwerking(
                self.uzb_sleutel, self.iso_jaar, self.iso_week,
                label=f"{medewerker.naam} (apart gefactureerd)",
            )
            deel.medewerkers.append(medewerker)
            delen.append(deel)
        apart_namen = {d.medewerkers[0].naam for d in delen}
        for melding in self.meldingen:
            naam = melding.split(":", 1)[0].strip()
            if naam in apart_namen:
                next(d for d in delen if d.medewerkers[0].naam == naam).meldingen.append(melding)
            else:
                hoofd.meldingen.append(melding)
        return hoofd, delen


def ontbrekende_loonschalen(verwerking: WeekVerwerking) -> list[str]:
    """Wie in deze week geen loonschaal heeft, op naam.

    Zonder loonschaal is er geen tarief en dus geen bedrag. Zo iemand telt wel
    mee in de uren, waardoor het weektotaal te laag uitkomt. De week wordt
    desondanks verwerkt (een ontbrekende schaal van een enkeling mag de rest
    niet ophouden); het overzicht en de factuurcontrole markeren deze personen
    en vragen om de schaal in te vullen en de week opnieuw te verwerken.
    """
    return sorted(m.naam for m in verwerking.medewerkers if not m.loonschaal)


def melding_zonder_tarief(verwerking: WeekVerwerking) -> str | None:
    """De waarschuwing bovenaan het overzicht als niet iedereen een tarief heeft."""
    zonder = verwerking.zonder_tarief
    if not zonder:
        return None
    namen = "; ".join(f"{m.naam} ({m.tarief_ontbreekt_omdat})" for m in zonder)
    return (
        f"LET OP: {len(zonder)} van de {len(verwerking.medewerkers)} "
        f"uitzendkrachten zonder tarief: {namen}. Hun uren staan in het "
        "overzicht, maar zonder bedrag; het weektotaal is dus te laag. Vul de "
        "loonschaal in bij Uitzendkrachten (of laad een tariefkaart met die "
        "schaal) en verwerk de week opnieuw; daarna is ook hun factuurregel te "
        "controleren."
    )


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
    elders_bekend: dict[str, str] | None = None,
    apart_gefactureerd: set[str] | None = None,
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

    Het Nitea-overzicht bevat soms mensen van een ánder bureau (het is een
    urenoverzicht, geen bureau-overzicht). Wie niet in de SNOOP-export van
    deze week staat maar wel bij een ander bureau bekend is
    (`elders_bekend`: naam -> bureaunaam), hoort niet in deze week: hij wordt
    overgeslagen met een melding. Staat hij wél in SNOOP, dan werkt hij deze
    week voor dit bureau en telt hij gewoon mee.

    `apart_gefactureerd` markeert wie door het bureau los gefactureerd wordt;
    zie `WeekVerwerking.gesplitst`.
    """
    verwerking = WeekVerwerking(uzb_sleutel, iso_jaar, iso_week)
    reeks = kaart if isinstance(kaart, Kaartreeks) else Kaartreeks.van_kaart(kaart)
    snoop_op_naam = {normaliseer_naam(s.naam): s for s in snoop}
    gezien: set[str] = set()

    for medewerker in nitea:
        sleutel = normaliseer_naam(medewerker.naam)
        gezien.add(sleutel)
        planning_bron = snoop_op_naam.get(sleutel)

        # Nitea en SNOOP schrijven dezelfde persoon niet altijd hetzelfde
        # ("Cristian" tegenover "Christian", "Robert Ionut" tegenover "Ionut
        # Robert"). Zonder koppeling krijgt zo iemand geen schaal terwijl SNOOP
        # die wel heeft. Een koppeling op gelijkenis wordt altijd gemeld.
        if planning_bron is None and snoop_op_naam:
            gevonden = beste_match(medewerker.naam, snoop_op_naam)
            if gevonden is not None and snoop_op_naam[gevonden].loonschaal:
                planning_bron = snoop_op_naam[gevonden]
                verwerking.meldingen.append(
                    f"{medewerker.naam}: gekoppeld aan '{planning_bron.naam}' uit "
                    f"SNOOP (loonschaal '{planning_bron.loonschaal}') -- de namen "
                    "verschillen. Controleer of dit dezelfde persoon is; laat "
                    "anders de spelling in Nitea of SNOOP gelijktrekken."
                )

        elders = (elders_bekend or {}).get(sleutel)
        if elders and planning_bron is None:
            verwerking.meldingen.append(
                f"{medewerker.naam}: staat op de uitzendkrachtenlijst van {elders} "
                "en niet in de SNOOP-export van deze week -- niet meegeteld in "
                "deze week. Hoort hij hier wél, zet hem dan bij Uitzendkrachten "
                "onder dit bureau."
            )
            continue

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
            if not loonschaal:
                # Ook de uitzendkrachtenlijst kan een andere spelling hebben.
                gevonden = beste_match(medewerker.naam, bekende_loonschalen)
                if gevonden is not None:
                    loonschaal = bekende_loonschalen[gevonden]
                    verwerking.meldingen.append(
                        f"{medewerker.naam}: geen eigen regel gevonden; de "
                        f"loonschaal '{loonschaal}' is overgenomen van "
                        f"'{gevonden}' uit de uitzendkrachtenlijst, omdat de "
                        "namen op elkaar lijken. Controleer of dit dezelfde "
                        "persoon is."
                    )
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
        reden = None
        if loonschaal and not heeft_tarief and not reeks.is_leeg:
            # Hoort de schaal bij een ánder bureau ("D2 SW" bij iemand onder
            # Level One), zeg dat dan. Anders blijft het een stil nulbedrag
            # waarvan niemand de oorzaak ziet.
            ander = _ander_bureau(loonschaal, uzb_sleutel)
            if ander:
                reden = (
                    f"loonschaal '{loonschaal}' hoort bij {ander}, niet bij "
                    f"{_bureaunaam(uzb_sleutel)}"
                )
                verwerking.meldingen.append(
                    f"{medewerker.naam}: {reden} -- geen bedrag berekend. "
                    f"Verplaats deze uitzendkracht bij Uitzendkrachten naar "
                    f"{ander}, of zet de juiste schaal van "
                    f"{_bureaunaam(uzb_sleutel)} erbij."
                )
            else:
                reden = f"loonschaal '{loonschaal}' staat niet op de tariefkaart"
                verwerking.meldingen.append(
                    f"{medewerker.naam}: geen tarief voor loonschaal "
                    f"'{loonschaal}' (kaartcode {kaartcode}) -- geen bedrag berekend"
                )
        elif not loonschaal:
            verwerking.meldingen.append(
                f"{medewerker.naam}: geen loonschaal bekend -- "
                f"{resultaat.netto_uren} uur zonder bedrag"
            )

        bedrag = bereken_bedrag(resultaat, schalen, conventies)
        if bedrag.regels and bedrag.ontbrekende_minuten:
            # Deels afgerekend: de kaart mist een tariefkolom voor uren die er
            # wel zijn. Melden, anders is het bedrag stilzwijgend te laag.
            ontbrekend = ", ".join(
                f"{categorie} ({Decimal(minuten) / Decimal(60):.2f} u)"
                for categorie, minuten in sorted(bedrag.ontbrekende_minuten.items())
            )
            verwerking.meldingen.append(
                f"{medewerker.naam}: de tariefkaart van "
                f"{_bureaunaam(uzb_sleutel)} heeft geen tarief voor {ontbrekend}; "
                "die uren staan wel in het overzicht maar niet in het bedrag. "
                "Vul de ontbrekende tariefkolom aan bij Lonen & tarieven."
            )
        verwerking.medewerkers.append(
            MedewerkerResultaat(
                naam=medewerker.naam,
                nitea_id=medewerker.nitea_id,
                loonschaal=loonschaal,
                kaartcode=kaartcode,
                resultaat=resultaat,
                bedrag=bedrag,
                registratie=list(medewerker.registratie),
                afwijkingen=resultaat.afwijkingen,
                apart=sleutel in (apart_gefactureerd or set()),
                tarief_reden=reden,
            )
        )

    verwerking.medewerkers.sort(key=lambda m: m.naam)
    return verwerking
