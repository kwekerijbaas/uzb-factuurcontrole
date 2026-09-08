"""Inkoopbedrag per uitzendkracht per week (SPEC §5).

Neemt de uitkomst van de calc-engine (minuten per toeslag-bron) en rekent die
af tegen de tariefkaart van het uitzendbureau, volgens diens conventies.

Een loontabel gaat in op een vaste datum, niet op een weekgrens. Loopt zo'n
datum midden door de week (01-07-2026 valt op woensdag, 01-08-2026 op zaterdag),
dan worden de minuten per tariefperiode apart geteld en tegen het tarief van
díe periode afgerekend.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.calc.engine import rond_op_kwartier
from app.services.calc.types import WeekResultaat

from .types import (
    BedragRegel,
    BedragResultaat,
    SchaalTarief,
    Schaalreeks,
    UzbConventies,
)

_CENT = Decimal("0.01")


def _als_reeks(schaal: SchaalTarief | Schaalreeks | None) -> Schaalreeks:
    return schaal if isinstance(schaal, Schaalreeks) else Schaalreeks.constant(schaal)


def _minuten_per_periode_en_bron(
    resultaat: WeekResultaat, reeks: Schaalreeks, afronden: bool
) -> dict[tuple[int, str], int]:
    """Gewerkte minuten per (tariefperiode, toeslag-bron).

    De kwartier-afronding loopt over alle emmertjes tegelijk, zodat het
    weektotaal behouden blijft ook als de week over twee tarieven verdeeld is
    (SPEC §4).
    """
    per_emmer: dict[tuple[int, str], int] = {}
    for seg in resultaat.trace:
        sleutel = (reeks.index_op(seg.datum), seg.bron)
        per_emmer[sleutel] = per_emmer.get(sleutel, 0) + seg.minuut_tot - seg.minuut_van
    return rond_op_kwartier(per_emmer) if afronden else per_emmer


def minuten_per_categorie(
    resultaat: WeekResultaat, conv: UzbConventies, afronden: bool = True
) -> tuple[dict[str, int], dict[str, tuple[str, ...]]]:
    """Verdeel de gewerkte minuten over de tariefcategorieën van deze UZB.

    Bronnen die de UZB niet doorbelast (bv. de dag-grens bij Sterk Werk) vallen
    terug op het basistarief in plaats van te verdwijnen — de uren zijn immers
    wel gewerkt.
    """
    per_categorie: dict[str, int] = {}
    bronnen: dict[str, list[str]] = {}
    for (_, bron), minuten in _minuten_per_periode_en_bron(
        resultaat, Schaalreeks.constant(None), afronden
    ).items():
        categorie = _categorie_van(conv, bron)
        per_categorie[categorie] = per_categorie.get(categorie, 0) + minuten
        bronnen.setdefault(categorie, []).append(bron)

    return per_categorie, {c: tuple(sorted(b)) for c, b in bronnen.items()}


def _categorie_van(conv: UzbConventies, bron: str) -> str:
    # niet doorbelaste toeslag (bv. dag_grens bij SW): tegen basistarief
    return conv.bron_naar_categorie.get(bron) or conv.bron_naar_categorie["normaal"]


def bereken_bedrag(
    resultaat: WeekResultaat,
    schaal: SchaalTarief | Schaalreeks | None,
    conv: UzbConventies,
    afronden: bool = True,
) -> BedragResultaat:
    """Reken de week van één uitzendkracht af tegen de tariefkaart.

    `schaal` mag één tarief zijn (de hele week hetzelfde) of een `Schaalreeks`
    wanneer er binnen de week een nieuwe loontabel ingaat.

    Zonder (of met een onvolledige) tariefkaart worden de betreffende uren niet
    meegerekend maar in `ontbrekende_tarieven` gemeld — een €0-tarief zou het
    gemiddelde vertekenen (SPEC §7).
    """
    uitkomst = BedragResultaat()
    reeks = _als_reeks(schaal)
    per_emmer = _minuten_per_periode_en_bron(resultaat, reeks, afronden)

    # Per periode de minuten samenvoegen tot tariefcategorieën.
    per_periode: dict[int, dict[str, int]] = {}
    bronnen: dict[tuple[int, str], list[str]] = {}
    for (periode, bron), minuten in per_emmer.items():
        categorie = _categorie_van(conv, bron)
        per_periode.setdefault(periode, {})
        per_periode[periode][categorie] = per_periode[periode].get(categorie, 0) + minuten
        bronnen.setdefault((periode, categorie), []).append(bron)

    ontbreekt: set[str] = set()
    for periode in sorted(per_periode):
        tarieven = reeks.periodes[periode][1] if periode < len(reeks.periodes) else None
        for categorie in sorted(per_periode[periode]):
            minuten = per_periode[periode][categorie]
            tarief = tarieven.tarief(categorie) if tarieven else None
            if tarief is None:
                ontbreekt.add(categorie)
                continue
            uitkomst.regels.append(
                BedragRegel(
                    categorie=categorie,
                    bronnen=tuple(sorted(bronnen.get((periode, categorie), ()))),
                    minuten=minuten,
                    tarief=tarief,
                    bedrag=(Decimal(minuten) / Decimal(60) * tarief).quantize(_CENT),
                    vanaf=_vanaf(reeks, periode),
                )
            )

    uitkomst.ontbrekende_tarieven = sorted(ontbreekt)
    return uitkomst


def _vanaf(reeks: Schaalreeks, periode: int) -> date | None:
    """De ingangsdatum van deze tariefperiode; leeg als de week één tarief heeft."""
    if reeks.is_constant or periode >= len(reeks.periodes):
        return None
    vanaf = reeks.periodes[periode][0]
    return None if vanaf == date.min else vanaf
