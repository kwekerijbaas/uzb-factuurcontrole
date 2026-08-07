"""Factuurcontrole: het urenoverzicht naast de UZB-factuur (SPEC §7).

Koppelt elke factuurregel aan een uitzendkracht uit ons eigen overzicht en
classificeert de verschillen. De namen op de factuur staan als initialen plus
achternaam ("K.P. Sliwa (Kamil)", "A.I. Boca") terwijl wij voluit werken
("Kamil Sliwa", "Adelina Iuliana Boca"); het koppelen gebeurt daarom op
achternaam, met de voornaam of initiaal als scheidsrechter bij naamgenoten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from rapidfuzz import fuzz

from app.services.ingest.factuur import Factuur, FactuurKracht
from app.services.verwerking import MedewerkerResultaat, WeekVerwerking

# soorten bevindingen
SOORT_UREN = "uren"
SOORT_BEDRAG = "bedrag"
SOORT_NIET_GEFACTUREERD = "niet_gefactureerd"
SOORT_NIET_IN_OVERZICHT = "niet_in_overzicht"
SOORT_GEEN_KOPPELING = "geen_koppeling"

# Level One draagt drie decimalen in het uurtarief (28,942 vs 28,94); over een
# week loopt dat op tot enkele centen. Dat is ruis, geen afwijking.
_CENT_TOLERANTIE = Decimal("0.50")
_UUR_TOLERANTIE = Decimal("0.01")


@dataclass
class Bevinding:
    soort: str
    naam: str
    uren_overzicht: Decimal | None = None
    uren_factuur: Decimal | None = None
    bedrag_overzicht: Decimal | None = None
    bedrag_factuur: Decimal | None = None
    melding: str = ""

    @property
    def uren_verschil(self) -> Decimal | None:
        if self.uren_overzicht is None or self.uren_factuur is None:
            return None
        return self.uren_factuur - self.uren_overzicht

    @property
    def bedrag_verschil(self) -> Decimal | None:
        if self.bedrag_overzicht is None or self.bedrag_factuur is None:
            return None
        return self.bedrag_factuur - self.bedrag_overzicht


@dataclass
class Controle:
    uzb_naam: str
    iso_jaar: int
    iso_week: int
    factuurnummers: list[str] = field(default_factory=list)
    koppelingen: list[tuple[MedewerkerResultaat, FactuurKracht]] = field(default_factory=list)
    bevindingen: list[Bevinding] = field(default_factory=list)
    uren_overzicht: Decimal = Decimal("0")
    uren_factuur: Decimal = Decimal("0")
    bedrag_overzicht: Decimal = Decimal("0")
    bedrag_factuur: Decimal = Decimal("0")

    @property
    def bedrag_verschil(self) -> Decimal:
        return self.bedrag_factuur - self.bedrag_overzicht

    @property
    def uren_verschil(self) -> Decimal:
        return self.uren_factuur - self.uren_overzicht


def _delen(naam: str) -> tuple[str, set[str]]:
    """Splits een naam in achternaam en de overige naamdelen (kleine letters).

    "K.P. Sliwa (Kamil)" -> ("sliwa", {"k", "p", "kamil"})
    "Adelina Iuliana Boca" -> ("boca", {"adelina", "iuliana"})
    """
    tekst = re.sub(r"\s+", " ", str(naam or "")).strip()
    haakjes = re.findall(r"\(([^)]*)\)", tekst)
    tekst = re.sub(r"\([^)]*\)", " ", tekst).strip()
    woorden = [w for w in re.split(r"\s+", tekst) if w]
    if not woorden:
        return "", set()
    achternaam = woorden[-1].lower()
    overig = {
        deel.lower().strip(".")
        for woord in woorden[:-1]
        for deel in woord.split(".")
        if deel.strip(".")
    }
    overig |= {h.lower() for h in haakjes if h.strip()}
    return achternaam, overig


def _past(medewerker_naam: str, factuur_naam: str) -> int:
    """Score voor het koppelen; 0 betekent geen match."""
    m_achter, m_overig = _delen(medewerker_naam)
    f_achter, f_overig = _delen(factuur_naam)
    if not m_achter or not f_achter:
        return 0

    gelijkenis = fuzz.ratio(m_achter, f_achter)
    if gelijkenis < 85:
        return 0

    score = int(gelijkenis)
    # voornaam of initiaal erbij laat naamgenoten uit elkaar houden
    if m_overig & f_overig:
        score += 40
    elif any(
        voor[0] == initiaal
        for voor in m_overig
        for initiaal in f_overig
        if len(initiaal) == 1 and voor
    ):
        score += 20
    return score


def koppel(
    medewerkers: list[MedewerkerResultaat], krachten: list[FactuurKracht]
) -> tuple[list[tuple[MedewerkerResultaat, FactuurKracht]], list[MedewerkerResultaat], list[FactuurKracht]]:
    """Koppel factuurregels aan medewerkers; retourneert ook wat overblijft."""
    mogelijk: list[tuple[int, int, int]] = []
    for i, medewerker in enumerate(medewerkers):
        for j, kracht in enumerate(krachten):
            score = _past(medewerker.naam, kracht.naam_ruw)
            if score:
                mogelijk.append((score, i, j))
    mogelijk.sort(key=lambda x: -x[0])

    gekoppeld: list[tuple[MedewerkerResultaat, FactuurKracht]] = []
    gebruikt_m: set[int] = set()
    gebruikt_k: set[int] = set()
    for _, i, j in mogelijk:
        if i in gebruikt_m or j in gebruikt_k:
            continue
        gebruikt_m.add(i)
        gebruikt_k.add(j)
        gekoppeld.append((medewerkers[i], krachten[j]))

    return (
        gekoppeld,
        [m for i, m in enumerate(medewerkers) if i not in gebruikt_m],
        [k for j, k in enumerate(krachten) if j not in gebruikt_k],
    )


def controleer(
    verwerking: WeekVerwerking, factuur: Factuur, uzb_naam: str
) -> Controle:
    """Leg het urenoverzicht naast de factuur en classificeer de verschillen."""
    controle = Controle(
        uzb_naam=uzb_naam,
        iso_jaar=verwerking.iso_jaar,
        iso_week=verwerking.iso_week,
        factuurnummers=list(factuur.factuurnummers),
    )

    # Sommige facturen splitsen één uitzendkracht over meerdere blokken (een
    # nagekomen dag, of een paginagrens). Tel die eerst bij elkaar op.
    samengevoegd: dict[tuple[str, frozenset[str]], FactuurKracht] = {}
    for kracht in factuur.krachten:
        sleutel = _delen(kracht.naam_ruw)
        sleutel = (sleutel[0], frozenset(sleutel[1]))
        if sleutel in samengevoegd:
            samengevoegd[sleutel].regels.extend(kracht.regels)
        else:
            samengevoegd[sleutel] = FactuurKracht(kracht.naam_ruw, list(kracht.regels))
    krachten = list(samengevoegd.values())

    gekoppeld, zonder_factuur, zonder_overzicht = koppel(verwerking.medewerkers, krachten)
    controle.koppelingen = gekoppeld

    for medewerker, kracht in sorted(gekoppeld, key=lambda p: p[0].naam):
        controle.uren_overzicht += medewerker.netto_uren
        controle.uren_factuur += kracht.uren
        controle.bedrag_overzicht += medewerker.bedrag.totaal
        controle.bedrag_factuur += kracht.bedrag

        uren_af = kracht.uren - medewerker.netto_uren
        bedrag_af = kracht.bedrag - medewerker.bedrag.totaal

        if abs(uren_af) > _UUR_TOLERANTIE:
            controle.bevindingen.append(
                Bevinding(
                    soort=SOORT_UREN,
                    naam=medewerker.naam,
                    uren_overzicht=medewerker.netto_uren,
                    uren_factuur=kracht.uren,
                    bedrag_overzicht=medewerker.bedrag.totaal,
                    bedrag_factuur=kracht.bedrag,
                    melding=(
                        f"gefactureerd {kracht.uren:.2f} u tegenover "
                        f"{medewerker.netto_uren:.2f} u volgens Nitea"
                    ),
                )
            )
        elif abs(bedrag_af) > _CENT_TOLERANTIE:
            controle.bevindingen.append(
                Bevinding(
                    soort=SOORT_BEDRAG,
                    naam=medewerker.naam,
                    uren_overzicht=medewerker.netto_uren,
                    uren_factuur=kracht.uren,
                    bedrag_overzicht=medewerker.bedrag.totaal,
                    bedrag_factuur=kracht.bedrag,
                    melding=(
                        f"uren kloppen, bedrag wijkt {bedrag_af:+.2f} af — "
                        "controleer loonschaal of toeslagverdeling"
                    ),
                )
            )

    for medewerker in sorted(zonder_factuur, key=lambda m: m.naam):
        if not medewerker.netto_uren:
            continue
        controle.uren_overzicht += medewerker.netto_uren
        controle.bedrag_overzicht += medewerker.bedrag.totaal
        controle.bevindingen.append(
            Bevinding(
                soort=SOORT_NIET_GEFACTUREERD,
                naam=medewerker.naam,
                uren_overzicht=medewerker.netto_uren,
                bedrag_overzicht=medewerker.bedrag.totaal,
                melding=(
                    f"{medewerker.netto_uren:.2f} u gewerkt, staat niet op de factuur"
                ),
            )
        )

    for kracht in sorted(zonder_overzicht, key=lambda k: k.naam_ruw):
        controle.uren_factuur += kracht.uren
        controle.bedrag_factuur += kracht.bedrag
        controle.bevindingen.append(
            Bevinding(
                soort=SOORT_NIET_IN_OVERZICHT,
                naam=kracht.naam_ruw,
                uren_factuur=kracht.uren,
                bedrag_factuur=kracht.bedrag,
                melding=(
                    f"{kracht.uren:.2f} u gefactureerd, komt niet voor in onze registratie"
                ),
            )
        )

    return controle


_LABELS = {
    SOORT_UREN: "Uren wijken af",
    SOORT_BEDRAG: "Bedrag wijkt af",
    SOORT_NIET_GEFACTUREERD: "Wel gewerkt, niet gefactureerd",
    SOORT_NIET_IN_OVERZICHT: "Wel gefactureerd, niet in onze registratie",
}


def bevindingenmail(controles: list[Controle]) -> str:
    """Stel de concept-bevindingenmail op voor HR en de uitzendbureaus."""
    regels = [
        "Hoi,",
        "",
        "Ik heb de ontvangen UZB-facturen vergeleken met onze eigen "
        "urenoverzichten (Nitea-uren gekoppeld aan de tariefkaart). Hieronder "
        "per uitzendbureau wat er opvalt.",
        "",
    ]

    for nummer, controle in enumerate(controles, start=1):
        regels.append(
            f"{nummer}. {controle.uzb_naam} — week {controle.iso_week}/{controle.iso_jaar}"
        )
        if controle.factuurnummers:
            regels.append(f"   Factuur: {', '.join(controle.factuurnummers)}")
        richting = "meer" if controle.bedrag_verschil > 0 else "minder"
        regels.append(
            f"   Gefactureerd EUR {controle.bedrag_factuur:,.2f} tegenover "
            f"EUR {controle.bedrag_overzicht:,.2f} berekend "
            f"({abs(controle.bedrag_verschil):,.2f} {richting}); "
            f"{controle.uren_factuur:.2f} u tegenover {controle.uren_overzicht:.2f} u."
        )

        if not controle.bevindingen:
            regels += ["   Geen afwijkingen.", ""]
            continue

        per_soort: dict[str, list[Bevinding]] = {}
        for bevinding in controle.bevindingen:
            per_soort.setdefault(bevinding.soort, []).append(bevinding)

        for soort in (
            SOORT_UREN,
            SOORT_NIET_GEFACTUREERD,
            SOORT_NIET_IN_OVERZICHT,
            SOORT_BEDRAG,
        ):
            groep = per_soort.get(soort)
            if not groep:
                continue
            regels.append(f"   {_LABELS[soort]}:")
            for bevinding in groep:
                regels.append(f"     - {bevinding.naam}: {bevinding.melding}")
        regels.append("")

    regels += [
        "Willen jullie vooral naar de uren-afwijkingen kijken; dat zijn de "
        "posten die tot een nafactuur of creditering kunnen leiden. Verschillen "
        "in alleen het bedrag wijzen meestal op een afwijkende loonschaal.",
        "",
        "Vragen? Hoor het graag.",
    ]
    return "\n".join(regels)
