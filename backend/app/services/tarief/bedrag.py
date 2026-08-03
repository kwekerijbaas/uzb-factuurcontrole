"""Inkoopbedrag per uitzendkracht per week (SPEC §5).

Neemt de uitkomst van de calc-engine (minuten per toeslag-bron) en rekent die
af tegen de tariefkaart van het uitzendbureau, volgens diens conventies.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.calc.engine import minuten_per_bron, rond_op_kwartier
from app.services.calc.types import WeekResultaat

from .types import BedragRegel, BedragResultaat, SchaalTarief, UzbConventies

_CENT = Decimal("0.01")


def minuten_per_categorie(
    resultaat: WeekResultaat, conv: UzbConventies, afronden: bool = True
) -> tuple[dict[str, int], dict[str, tuple[str, ...]]]:
    """Verdeel de gewerkte minuten over de tariefcategorieën van deze UZB.

    Bronnen die de UZB niet doorbelast (bv. de dag-grens bij Sterk Werk) vallen
    terug op het basistarief in plaats van te verdwijnen — de uren zijn immers
    wel gewerkt.
    """
    per_bron = minuten_per_bron(resultaat.trace)
    if afronden:
        per_bron = rond_op_kwartier(per_bron)

    per_categorie: dict[str, int] = {}
    bronnen: dict[str, list[str]] = {}
    for bron, minuten in per_bron.items():
        categorie = conv.bron_naar_categorie.get(bron)
        if categorie is None:
            # niet doorbelaste toeslag (bv. dag_grens bij SW): tegen basistarief
            categorie = conv.bron_naar_categorie["normaal"]
        per_categorie[categorie] = per_categorie.get(categorie, 0) + minuten
        bronnen.setdefault(categorie, []).append(bron)

    return per_categorie, {c: tuple(sorted(b)) for c, b in bronnen.items()}


def bereken_bedrag(
    resultaat: WeekResultaat,
    schaal: SchaalTarief | None,
    conv: UzbConventies,
    afronden: bool = True,
) -> BedragResultaat:
    """Reken de week van één uitzendkracht af tegen de tariefkaart.

    Zonder (of met een onvolledige) tariefkaart worden de betreffende uren niet
    meegerekend maar in `ontbrekende_tarieven` gemeld — een €0-tarief zou het
    gemiddelde vertekenen (SPEC §7).
    """
    uitkomst = BedragResultaat()
    per_categorie, bronnen = minuten_per_categorie(resultaat, conv, afronden)

    if schaal is None:
        uitkomst.ontbrekende_tarieven = sorted(per_categorie)
        return uitkomst

    for categorie in sorted(per_categorie):
        minuten = per_categorie[categorie]
        tarief = schaal.tarief(categorie)
        if tarief is None:
            uitkomst.ontbrekende_tarieven.append(categorie)
            continue
        bedrag = (Decimal(minuten) / Decimal(60) * tarief).quantize(_CENT)
        uitkomst.regels.append(
            BedragRegel(
                categorie=categorie,
                bronnen=bronnen.get(categorie, ()),
                minuten=minuten,
                tarief=tarief,
                bedrag=bedrag,
            )
        )

    return uitkomst
