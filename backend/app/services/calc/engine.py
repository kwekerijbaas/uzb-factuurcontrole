"""CAO Glastuinbouw calculatie-engine.

Werkwijze per uitzendkracht × week:

1. Zet elke Nitea-registratieregel om in klok-minuten (split op middernacht voor
   nachtdiensten).
2. Bepaal per minuut de tijdgebonden toeslag (onregelmatige uren) uit de
   bewerkbare `ToeslagRegel`-set.
3. Trek de Nitea-pauze af door per registratieregel de minuten met de LAAGSTE
   toeslag als pauze te markeren (pauze valt buiten toeslagvensters waar mogelijk).
4. Loop de gewerkte minuten chronologisch door en bepaal overwerk (> weeknorm,
   35%), daggrens (> 10u/dag, 50%) en weekgrens (> 48u/week, 50%).
5. Per minuut geldt de HOOGSTE toeslag (CAO art. 28 lid 2b — geen cumulatie).
6. Aggregeer minuten per percentage en vergelijk met de SNOOP-planning.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TypeVar

from .types import (
    SOORT_GEEN_PLANNING,
    SOORT_GEEN_REGISTRATIE,
    SOORT_REGISTRATIE_INCONSISTENT,
    SOORT_TIJD_VERSCHIL,
    SOORT_UREN_VERSCHIL,
    Afwijking,
    PlanningRegel,
    RegistratieRegel,
    ToeslagRegel,
    TraceSegment,
    WeekParameters,
    WeekResultaat,
)

_KWARTIER = 15
_K = TypeVar("_K")


def rond_op_kwartier(minuten_per_bucket: dict[_K, int]) -> dict[_K, int]:
    """Rond elke bucket af op kwartieren, met behoud van het weektotaal
    (SPEC §4). Nitea-uren staan al op kwartieren, dus het totaal is een veelvoud
    van 15; de afrondingsrest wordt op de grootste bucket gelegd.

    Werkt op zowel percentage- als bron-buckets (zie `minuten_per_bron`)."""
    afgerond = {
        sleutel: int(round(m / _KWARTIER) * _KWARTIER)
        for sleutel, m in minuten_per_bucket.items()
    }
    totaal = sum(minuten_per_bucket.values())
    doel = int(round(totaal / _KWARTIER) * _KWARTIER)
    rest = doel - sum(afgerond.values())
    if rest and afgerond:
        grootste = max(afgerond, key=lambda s: afgerond[s])
        afgerond[grootste] += rest
    return {sleutel: m for sleutel, m in afgerond.items() if m}


def minuten_per_bron(trace: list[TraceSegment]) -> dict[str, int]:
    """Aggregeer de trace naar minuten per toeslag-bron.

    Nodig omdat één percentage meerdere tarieven kan hebben: nacht, avond,
    zaterdagmiddag en feestdag vallen alle onder 50%, maar Sterk Werk kent een
    apart nachtuur-tarief en Level One een apart feestdag-tarief (SPEC §5)."""
    per_bron: dict[str, int] = defaultdict(int)
    for seg in trace:
        per_bron[seg.bron] += seg.minuut_tot - seg.minuut_van
    return dict(per_bron)


def _naar_minuut(t: time) -> int:
    return t.hour * 60 + t.minute


def _span(regel: RegistratieRegel) -> tuple[datetime, datetime]:
    start = datetime.combine(regel.datum, regel.begin)
    eind = datetime.combine(regel.datum, regel.eind)
    if eind <= start:  # nachtdienst over middernacht
        eind += timedelta(days=1)
    return start, eind


def _tod_percentage(
    moment: datetime, regels: list[ToeslagRegel], feestdagen: frozenset[date]
) -> tuple[Decimal, str]:
    """Hoogste tijdgebonden toeslag voor één minuut. Retourneert (pct, bron)."""
    iso = moment.isoweekday()
    minuut = moment.hour * 60 + moment.minute
    is_feest = moment.date() in feestdagen
    beste = Decimal("0")
    bron = "normaal"
    for r in regels:
        if r.matcht(iso, minuut, is_feest):
            if r.percentage > beste or (r.percentage == beste and bron == "normaal"):
                beste = r.percentage
                bron = r.code
    return beste, bron


def _verzamel_minuten(
    registratie: list[RegistratieRegel],
    regels: list[ToeslagRegel],
    feestdagen: frozenset[date],
    afwijkingen: list[Afwijking],
    params: WeekParameters,
) -> list[tuple[datetime, Decimal, str]]:
    """Geeft de gewerkte minuten (na pauze-aftrek) met hun tijdgebonden toeslag,
    chronologisch gesorteerd."""
    gewerkt: list[tuple[datetime, Decimal, str]] = []
    for regel in registratie:
        start, eind = _span(regel)
        totaal = int((eind - start).total_seconds() // 60)
        minuten = []
        for i in range(totaal):
            m = start + timedelta(minutes=i)
            pct, bron = _tod_percentage(m, regels, feestdagen)
            minuten.append((m, pct, bron))

        # Nitea 'Werk tijd' is leidend: de niet-gewerkte tijd binnen de bracket
        # (pauze én eventuele onderbrekingen bij split shifts) wordt weggehaald,
        # zodat het gewerkte totaal exact de Nitea-nettotijd is. Bij ontbrekende
        # of onmogelijke werktijd vallen we terug op pure pauze-aftrek.
        if 0 < regel.gewerkte_minuten <= totaal:
            te_verwijderen = totaal - regel.gewerkte_minuten
        else:
            te_verwijderen = max(0, regel.pauze_minuten)

        # Informatief: bracket − pauze verklaart de werktijd niet (split shift
        # of afwijkende registratie). Kleine verschillen zijn Nitea's
        # kwartierafronding en worden niet gemeld.
        onverklaard = abs(totaal - regel.pauze_minuten - regel.gewerkte_minuten)
        if regel.gewerkte_minuten and onverklaard > params.tolerantie_registratie_minuten:
            afwijkingen.append(
                Afwijking(
                    datum=regel.datum,
                    soort=SOORT_REGISTRATIE_INCONSISTENT,
                    detail=(
                        f"begin/eind ({totaal} min) − pauze ({regel.pauze_minuten}) "
                        f"= {totaal - regel.pauze_minuten}, maar Nitea meldt "
                        f"{regel.gewerkte_minuten} gewerkte minuten; "
                        f"{te_verwijderen} min als niet-gewerkt afgetrokken"
                    ),
                    registratie_minuten=regel.gewerkte_minuten,
                )
            )

        # verwijder de niet-gewerkte minuten met de LAAGSTE toeslag (asc pct, dan
        # tijd) — zo blijven nacht-/avond-/zaterdaguren behouden.
        if te_verwijderen > 0:
            volgorde = sorted(range(len(minuten)), key=lambda i: (minuten[i][1], minuten[i][0]))
            weg_idx = set(volgorde[:te_verwijderen])
        else:
            weg_idx = set()
        gewerkt.extend(m for i, m in enumerate(minuten) if i not in weg_idx)

    gewerkt.sort(key=lambda x: x[0])
    return gewerkt


def _segmenteer_trace(rauw: list[tuple[datetime, Decimal, str]]) -> list[TraceSegment]:
    """Vat opeenvolgende minuten met dezelfde (datum, pct, bron) samen."""
    segmenten: list[TraceSegment] = []
    for moment, pct, bron in rauw:
        d = moment.date()
        mvd = moment.hour * 60 + moment.minute
        if (
            segmenten
            and segmenten[-1].datum == d
            and segmenten[-1].percentage == pct
            and segmenten[-1].bron == bron
            and segmenten[-1].minuut_tot == mvd
        ):
            segmenten[-1].minuut_tot = mvd + 1
        else:
            segmenten.append(TraceSegment(d, mvd, mvd + 1, pct, bron))
    return segmenten


def bereken_week(
    registratie: list[RegistratieRegel],
    planning: list[PlanningRegel],
    toeslag_regels: list[ToeslagRegel],
    feestdagen: frozenset[date] = frozenset(),
    parameters: WeekParameters | None = None,
) -> WeekResultaat:
    params = parameters or WeekParameters()
    afwijkingen: list[Afwijking] = []

    gewerkt = _verzamel_minuten(registratie, toeslag_regels, feestdagen, afwijkingen, params)

    minuten_per_pct: dict[Decimal, int] = defaultdict(int)
    rauw_trace: list[tuple[datetime, Decimal, str]] = []

    cum_week = 0
    cum_dag: dict[date, int] = defaultdict(int)

    for moment, tod_pct, tod_bron in gewerkt:
        cum_week += 1
        cum_dag[moment.date()] += 1

        kandidaten: list[tuple[Decimal, str]] = [(tod_pct, tod_bron)]
        if cum_week > params.weeknorm_minuten:
            kandidaten.append((params.overwerk_percentage, "overwerk_35"))
        if cum_dag[moment.date()] > params.dag_grens_minuten:
            kandidaten.append((params.dag_grens_percentage, "dag_grens_50"))
        if not params.week_50u_uitzondering and cum_week > params.week_grens_minuten:
            kandidaten.append((params.week_grens_percentage, "week_grens_50"))

        pct, bron = max(kandidaten, key=lambda k: k[0])
        minuten_per_pct[pct] += 1
        rauw_trace.append((moment, pct, bron))

    _vergelijk_planning(registratie, planning, afwijkingen, params)

    return WeekResultaat(
        netto_minuten=sum(minuten_per_pct.values()),
        minuten_per_percentage=dict(minuten_per_pct),
        afwijkingen=afwijkingen,
        trace=_segmenteer_trace(rauw_trace),
    )


def _vergelijk_planning(
    registratie: list[RegistratieRegel],
    planning: list[PlanningRegel],
    afwijkingen: list[Afwijking],
    params: WeekParameters,
) -> None:
    reg_per_dag: dict[date, int] = defaultdict(int)
    reg_tijden: dict[date, list[RegistratieRegel]] = defaultdict(list)
    for r in registratie:
        reg_per_dag[r.datum] += r.gewerkte_minuten
        reg_tijden[r.datum].append(r)

    plan_per_dag: dict[date, int] = defaultdict(int)
    plan_tijden: dict[date, list[PlanningRegel]] = defaultdict(list)
    for p in planning:
        plan_per_dag[p.datum] += p.geplande_minuten
        plan_tijden[p.datum].append(p)

    for d in sorted(set(reg_per_dag) | set(plan_per_dag)):
        rmin = reg_per_dag.get(d)
        pmin = plan_per_dag.get(d)
        if pmin is None:
            afwijkingen.append(
                Afwijking(d, SOORT_GEEN_PLANNING, "registratie zonder planning",
                          registratie_minuten=rmin)
            )
            continue
        if rmin is None:
            afwijkingen.append(
                Afwijking(d, SOORT_GEEN_REGISTRATIE, "planning zonder registratie",
                          planning_minuten=pmin)
            )
            continue
        if abs(rmin - pmin) > params.tolerantie_uren_minuten:
            afwijkingen.append(
                Afwijking(d, SOORT_UREN_VERSCHIL,
                          f"gepland {pmin} min, gewerkt {rmin} min",
                          planning_minuten=pmin, registratie_minuten=rmin)
            )
        # tijdvenster-afwijking (begin/eind), alleen bij gelijke urentelling relevant
        p0 = min(plan_tijden[d], key=lambda x: x.begin)
        r0 = min(reg_tijden[d], key=lambda x: x.begin)
        verschil_begin = abs(_naar_minuut(p0.begin) - _naar_minuut(r0.begin))
        verschil_eind = abs(_naar_minuut(p0.eind) - _naar_minuut(r0.eind))
        if max(verschil_begin, verschil_eind) > params.tolerantie_tijd_minuten:
            afwijkingen.append(
                Afwijking(d, SOORT_TIJD_VERSCHIL,
                          f"gepland {p0.begin:%H:%M}-{p0.eind:%H:%M}, "
                          f"geregistreerd {r0.begin:%H:%M}-{r0.eind:%H:%M}")
            )
