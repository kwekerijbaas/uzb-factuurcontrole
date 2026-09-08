"""Validatie van een geüploade tariefkaart en van factorwijzigingen (SPEC §6).

Doel: fouten opvangen vóórdat ze in de urencontrole doorwerken. Op de kaart per
01-01-2026 zat bijvoorbeeld een 135%-tarief van Cervokordaat C3 van EUR 29,80,
terwijl alle andere schalen van dat bureau 1,2455 x het basistarief hanteren --
het overwerktarief lag daar dus lager dan het normale tarief.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from decimal import Decimal

# ernst
FOUT = "fout"
WAARSCHUWING = "waarschuwing"

# soort
SOORT_UITSCHIETER = "uitschieter"
SOORT_ONTBREEKT = "ontbreekt"
SOORT_NIET_UNIFORM = "niet_uniform"
SOORT_ONDER_MINIMUMLOON = "onder_minimumloon"
SOORT_GEWIJZIGD = "gewijzigd"

_AFWIJKING_DREMPEL = Decimal("0.05")  # 5% t.o.v. de mediaan van de UZB


@dataclass
class Bevinding:
    ernst: str
    soort: str
    uzb_sleutel: str
    kaartcode: str | None
    categorie: str | None
    melding: str


def _mediaan(waarden: list[Decimal]) -> Decimal:
    return Decimal(str(statistics.median([float(w) for w in waarden])))


def valideer_tarieven(
    uzb_sleutel: str,
    tarieven: dict[str, dict[str, Decimal]],
    basis_categorie: str = "100",
) -> list[Bevinding]:
    """Controleer de onderlinge samenhang van de tarieven van één uitzendbureau.

    Per categorie wordt de verhouding tot het basistarief vergeleken met de
    mediaan over alle schalen; schalen die meer dan 5% afwijken zijn vrijwel
    altijd een typefout of een kapotte formule.
    """
    bevindingen: list[Bevinding] = []
    if not tarieven:
        return bevindingen

    # Jeugd- en volwassenschalen hebben eigen tariefverhoudingen; door elkaar
    # vergelijken levert valse meldingen op. Het jeugd-payroll-tabblad bevat
    # beide.
    families: dict[str, dict[str, dict[str, Decimal]]] = {}
    for kaartcode, rij in tarieven.items():
        familie = "jeugd" if kaartcode[:1].isdigit() else "volwassen"
        families.setdefault(familie, {})[kaartcode] = rij
    if len(families) > 1:
        for deel in families.values():
            bevindingen += valideer_tarieven(uzb_sleutel, deel, basis_categorie)
        return bevindingen

    categorieen = {c for rij in tarieven.values() for c in rij} - {basis_categorie}

    for categorie in sorted(categorieen):
        verhoudingen: dict[str, Decimal] = {}
        for kaartcode, rij in tarieven.items():
            basis, waarde = rij.get(basis_categorie), rij.get(categorie)
            if basis and waarde:
                verhoudingen[kaartcode] = waarde / basis
        if len(verhoudingen) < 3:
            continue  # te weinig referentie voor een zinvolle mediaan

        mediaan = _mediaan(list(verhoudingen.values()))
        for kaartcode, verhouding in sorted(verhoudingen.items()):
            if mediaan and abs(verhouding - mediaan) / mediaan > _AFWIJKING_DREMPEL:
                verwacht = (tarieven[kaartcode][basis_categorie] * mediaan).quantize(
                    Decimal("0.01")
                )
                bevindingen.append(
                    Bevinding(
                        ernst=FOUT,
                        soort=SOORT_UITSCHIETER,
                        uzb_sleutel=uzb_sleutel,
                        kaartcode=kaartcode,
                        categorie=categorie,
                        melding=(
                            f"tarief {tarieven[kaartcode][categorie]} wijkt af: "
                            f"{verhouding:.4f} x het basistarief terwijl de andere "
                            f"schalen {mediaan:.4f} x hanteren (verwacht ~{verwacht})"
                        ),
                    )
                )

    # gaten: een categorie die bijna overal voorkomt maar hier ontbreekt
    voorkomen: dict[str, int] = {}
    for rij in tarieven.values():
        for categorie in rij:
            voorkomen[categorie] = voorkomen.get(categorie, 0) + 1
    for categorie, aantal in sorted(voorkomen.items()):
        # alleen melden als de categorie voor de meerderheid van de schalen
        # geldt; anders is 'ontbreekt' juist de normale situatie
        if aantal * 2 <= len(tarieven):
            continue
        for kaartcode, rij in sorted(tarieven.items()):
            if categorie not in rij:
                bevindingen.append(
                    Bevinding(
                        ernst=WAARSCHUWING,
                        soort=SOORT_ONTBREEKT,
                        uzb_sleutel=uzb_sleutel,
                        kaartcode=kaartcode,
                        categorie=categorie,
                        melding=(
                            f"geen tarief, terwijl {aantal} van de {len(tarieven)} "
                            "schalen dit wel hebben"
                        ),
                    )
                )

    return bevindingen


def valideer_uniforme_factor(
    uzb_sleutel: str, factoren: list
) -> list[Bevinding]:
    """Controleer dat één factor per categorie geldt voor álle schalen.

    Sterk Werk en Cervokordaat hanteren contractueel één omrekenfactor per
    toeslagcategorie; een schaal die daarvan afwijkt is een invoerfout.
    """
    bevindingen: list[Bevinding] = []
    per_categorie: dict[str, dict[str, Decimal]] = {}
    for f in factoren:
        per_categorie.setdefault(f.categorie, {})[f.kaartcode] = f.factor

    for categorie, per_code in sorted(per_categorie.items()):
        if len(per_code) < 2:
            continue
        mediaan = _mediaan(list(per_code.values()))
        for kaartcode, factor in sorted(per_code.items()):
            if mediaan and abs(factor - mediaan) / mediaan > Decimal("0.01"):
                bevindingen.append(
                    Bevinding(
                        ernst=FOUT,
                        soort=SOORT_NIET_UNIFORM,
                        uzb_sleutel=uzb_sleutel,
                        kaartcode=kaartcode,
                        categorie=categorie,
                        melding=(
                            f"omrekenfactor {factor:.4f} wijkt af van de {mediaan:.4f} "
                            f"die de overige schalen van {uzb_sleutel} hanteren"
                        ),
                    )
                )
    return bevindingen


def valideer_minimumloon(
    loontabel, minimumloon: Decimal
) -> list[Bevinding]:
    """Signaleer CAO-lonen onder het wettelijk minimumloon (volwassenen)."""
    bevindingen: list[Bevinding] = []
    for schaal, loon in sorted(loontabel.lonen.items()):
        if schaal[0].isdigit():
            continue  # jeugdschaal: eigen (lager) minimum
        if loon < minimumloon:
            bevindingen.append(
                Bevinding(
                    ernst=FOUT,
                    soort=SOORT_ONDER_MINIMUMLOON,
                    uzb_sleutel="-",
                    kaartcode=schaal,
                    categorie=None,
                    melding=f"uurloon {loon} ligt onder het minimumloon {minimumloon}",
                )
            )
    return bevindingen


def vergelijk_factoren(
    uzb_sleutel: str, oud: list, nieuw: list, drempel: Decimal = Decimal("0.005")
) -> list[Bevinding]:
    """Verschiloverzicht bij het uploaden van een nieuwe tariefkaart.

    Toont per schaal en categorie wat de omrekenfactor doet, zodat een
    onbedoelde wijziging opvalt vóór bevestiging.
    """
    def sleutel(f):
        return (f.kaartcode, f.categorie)

    oude = {sleutel(f): f.factor for f in oud}
    nieuwe = {sleutel(f): f.factor for f in nieuw}
    bevindingen: list[Bevinding] = []

    for k in sorted(set(oude) | set(nieuwe)):
        kaartcode, categorie = k
        voor, na = oude.get(k), nieuwe.get(k)
        if voor is None:
            melding = f"nieuw: omrekenfactor {na:.4f}"
        elif na is None:
            melding = f"vervallen: had omrekenfactor {voor:.4f}"
        elif abs(na - voor) <= drempel:
            continue
        else:
            richting = "hoger" if na > voor else "lager"
            melding = (
                f"omrekenfactor {voor:.4f} -> {na:.4f} "
                f"({(na - voor):+.4f}, {richting})"
            )
        bevindingen.append(
            Bevinding(
                ernst=WAARSCHUWING,
                soort=SOORT_GEWIJZIGD,
                uzb_sleutel=uzb_sleutel,
                kaartcode=kaartcode,
                categorie=categorie,
                melding=melding,
            )
        )
    return bevindingen
