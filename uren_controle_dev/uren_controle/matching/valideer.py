"""Valideer een week aan facturen tegen doorgegeven uren + tarieventabel.

Output: lijst MatchResultaat-objecten, één per (medewerker × week).

Statussen:
  ok                            — uren én tarief én alle regels kloppen
  uren_afwijking                — totaal-uren factuur ≠ Excel (drempel overschreden)
  tarief_afwijking              — tarief op factuur ≠ tarieventabel (binnen tolerantie)
  contractvorm_switch           — tarief verandert binnen één week voor zelfde persoon
  niet_in_excel                 — staat op factuur, niet in Excel        → HARD ROOD
  niet_op_factuur               — staat in Excel, niet op factuur        → mogelijk Payroll
  ongebruikelijke_categorie     — 200% / bijz_150 / bereikbaarheid e.d.  → vlag
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

# Categorieën die als "gewone uren" tellen (vergelijken we met Excel)
UREN_CATEGORIEEN = {"100", "135", "150", "200", "bijz_150", "correctie"}
# Categorieën die opvallend zijn — niet automatisch fout, wel vlag
OPVALLENDE_CATEGORIEEN = {"200", "bijz_150", "bereikbaarheid", "reiskosten", "correctie"}


@dataclass
class TariefRegelInfo:
    """Per categorie + tarief op factuur, het bijbehorende loonschaal-resultaat."""
    categorie:        str
    uren:             float
    tarief_factuur:   float
    bedrag:           float
    loonschaal:       str = ""
    contractvorm:     str = ""
    tarief_in_tabel:  float = 0.0
    tarief_delta:     float = 0.0
    binnen_tolerantie: bool = True


@dataclass
class MatchResultaat:
    naam_factuur:    str
    naam_excel:      str
    nr_excel:        str
    week_nr:         int
    jaar:            int

    uren_factuur:    float
    uren_excel:      float
    uren_verschil:   float       # positief = meer gefactureerd dan doorgegeven

    bedrag_factuur:  float
    factuurnummers:  list[str] = field(default_factory=list)

    splits_factuur:  dict = field(default_factory=dict)   # {"100": uren, "135": uren, ...}
    tarief_regels:   list[TariefRegelInfo] = field(default_factory=list)

    statussen:       list[str] = field(default_factory=list)
    notities:        list[str] = field(default_factory=list)

    @property
    def is_te_veel_gefactureerd(self) -> bool:
        return self.uren_verschil > 0

    @property
    def hoofdstatus(self) -> str:
        # Volgorde van prioriteit: hard rood eerst
        prio = [
            "niet_in_excel",
            "uren_afwijking",
            "contractvorm_switch",
            "tarief_afwijking",
            "ongebruikelijke_categorie",
            "niet_op_factuur",
            "ok",
        ]
        for s in prio:
            if s in self.statussen:
                return s
        return "ok"


def valideer_week(
    week_doorgegeven,                # DoorgegevenWeek
    facturen: list,                  # list[FactuurL1]
    tarieven_db,                     # TarievenDatabase
    config: dict,                    # uit drempels.yaml
) -> list[MatchResultaat]:
    """Vergelijk alle factuurmedewerkers met doorgegeven uren.

    Belangrijke aannames:
      - Alle facturen horen bij dezelfde week + UZB.
      - tarieven_db bevat een tabblad `week_doorgegeven.uzb_code`.
      - config heeft 'totaal_uren_drempel', 'tarief_drempel' etc.
    """
    from .naam_matcher import match_naam

    drempel_uren = float(config.get("totaal_uren_drempel", 0.05))
    drempel_tarief = float(config.get("tarief_drempel", 0.02))

    # ── 1. Verzamel factuur-data per medewerker (samengevoegd over facturen) ──
    factuur_per_naam: dict[str, dict] = defaultdict(lambda: {
        "uren":       0.0,
        "bedrag":     0.0,
        "regels":     [],         # raw FactuurRegelL1's
        "factuurnrs": set(),
    })
    factuur_datum_iso = ""
    for f in facturen:
        if f.datum:
            try:
                d, mnd, jr = f.datum.split("-")
                factuur_datum_iso = f"{jr}-{int(mnd):02d}-{int(d):02d}"
            except Exception:
                pass
        for r in f.regels:
            data = factuur_per_naam[r.naam_factuur]
            data["uren"] += r.uren
            data["bedrag"] += r.bedrag
            data["regels"].append(r)
            data["factuurnrs"].add(f.factuurnummer)

    # ── 2. Verzamel doorgegeven-data per nr ──────────────────────────────────
    doorgegeven_per_nr = {r.nr: r for r in week_doorgegeven.regels}
    doorgegeven_gebruikt: set[str] = set()

    resultaten: list[MatchResultaat] = []

    # ── 3. Loop over factuur-medewerkers ─────────────────────────────────────
    for naam_fac, data in factuur_per_naam.items():
        match, score, methode = match_naam(naam_fac, week_doorgegeven.regels)

        if match is None:
            # Op factuur, niet in Excel → HARD ROOD
            r = MatchResultaat(
                naam_factuur=    naam_fac,
                naam_excel=      "",
                nr_excel=        "",
                week_nr=         week_doorgegeven.week_nr,
                jaar=            week_doorgegeven.jaar,
                uren_factuur=    round(data["uren"], 2),
                uren_excel=      0.0,
                uren_verschil=   round(data["uren"], 2),
                bedrag_factuur=  round(data["bedrag"], 2),
                factuurnummers=  sorted(data["factuurnrs"]),
            )
            r.statussen.append("niet_in_excel")
            r.notities.append(
                f"Geen tegenhanger in doorgegeven uren — €{data['bedrag']:.2f} "
                f"is potentieel ten onrechte gefactureerd."
            )
            _vul_tariefregels(r, data["regels"], tarieven_db, week_doorgegeven.uzb_code,
                              factuur_datum_iso, drempel_tarief)
            resultaten.append(r)
            continue

        doorgegeven_gebruikt.add(match.nr)

        # Splitsing per categorie (uren)
        splits: dict[str, float] = defaultdict(float)
        for fr in data["regels"]:
            if fr.categorie in UREN_CATEGORIEEN:
                splits[fr.categorie] += fr.uren

        uren_fac = round(sum(splits.values()), 2)
        uren_xls = round(match.totaal_uren, 2)
        verschil = round(uren_fac - uren_xls, 2)

        r = MatchResultaat(
            naam_factuur=    naam_fac,
            naam_excel=      match.naam,
            nr_excel=        match.nr,
            week_nr=         week_doorgegeven.week_nr,
            jaar=            week_doorgegeven.jaar,
            uren_factuur=    uren_fac,
            uren_excel=      uren_xls,
            uren_verschil=   verschil,
            bedrag_factuur=  round(data["bedrag"], 2),
            factuurnummers=  sorted(data["factuurnrs"]),
            splits_factuur=  {k: round(v, 2) for k, v in splits.items()},
        )

        # Status: uren-afwijking
        if abs(verschil) > drempel_uren:
            r.statussen.append("uren_afwijking")
            richt = "te veel gefactureerd" if verschil > 0 else "minder gefactureerd dan doorgegeven"
            r.notities.append(f"{richt}: factuur {uren_fac}u vs Excel {uren_xls}u (Δ {verschil:+.2f}u)")

        # Tarief-controle per regel
        _vul_tariefregels(r, data["regels"], tarieven_db, week_doorgegeven.uzb_code,
                          factuur_datum_iso, drempel_tarief)

        # Tarief-afwijking?
        if any(not t.binnen_tolerantie for t in r.tarief_regels):
            r.statussen.append("tarief_afwijking")
            for t in r.tarief_regels:
                if not t.binnen_tolerantie:
                    r.notities.append(
                        f"Tarief @ {t.categorie}: €{t.tarief_factuur:.4f} ≠ "
                        f"tabel €{t.tarief_in_tabel:.4f} ({t.loonschaal} {t.contractvorm}) "
                        f"Δ €{t.tarief_delta:+.4f}"
                    )

        # Contractvorm-switch binnen één persoon één week?
        contracten = {(t.loonschaal, t.contractvorm)
                      for t in r.tarief_regels
                      if t.loonschaal}
        if len(contracten) > 1:
            r.statussen.append("contractvorm_switch")
            cs = ", ".join(f"{ls} {cv}" for ls, cv in sorted(contracten))
            r.notities.append(f"Meerdere schalen/contractvormen binnen één week: {cs}")

        # Ongebruikelijke categorieën?
        opvallend = [c for c in r.splits_factuur if c in OPVALLENDE_CATEGORIEEN]
        if opvallend:
            r.statussen.append("ongebruikelijke_categorie")
            r.notities.append(f"Ongebruikelijke categorieën op factuur: {', '.join(opvallend)}")

        if not r.statussen:
            r.statussen.append("ok")

        resultaten.append(r)

    # ── 4. Doorgegeven medewerkers die niet op factuur staan ────────────────
    for nr, dr in doorgegeven_per_nr.items():
        if nr in doorgegeven_gebruikt:
            continue
        r = MatchResultaat(
            naam_factuur=    "",
            naam_excel=      dr.naam,
            nr_excel=        dr.nr,
            week_nr=         week_doorgegeven.week_nr,
            jaar=            week_doorgegeven.jaar,
            uren_factuur=    0.0,
            uren_excel=      round(dr.totaal_uren, 2),
            uren_verschil=  -round(dr.totaal_uren, 2),
            bedrag_factuur=  0.0,
        )
        r.statussen.append("niet_op_factuur")
        r.notities.append(
            f"Doorgegeven {dr.totaal_uren}u maar geen factuur-regel gevonden — "
            f"mogelijk op Payroll-factuur (buiten huidige scope)."
        )
        resultaten.append(r)

    return resultaten


def _vul_tariefregels(
    r: MatchResultaat,
    factuur_regels: list,
    tarieven_db,
    uzb_code: str,
    datum_iso: str,
    drempel: float,
):
    """Voor elke regel op de factuur: lookup loonschaal/contractvorm en delta."""
    PCT_MAP = {
        "100":      100,
        "135":      135,
        "150":      150,
        "200":      200,
        "bijz_150": "bijz_150",
    }
    for fr in factuur_regels:
        pct = PCT_MAP.get(fr.categorie)
        if pct is None or fr.tarief == 0:
            r.tarief_regels.append(TariefRegelInfo(
                categorie=         fr.categorie,
                uren=              fr.uren,
                tarief_factuur=    fr.tarief,
                bedrag=            fr.bedrag,
                binnen_tolerantie= True,
            ))
            continue
        match = tarieven_db.zoek(uzb_code, fr.tarief, pct, datum_iso, tolerantie=drempel)
        if match:
            r.tarief_regels.append(TariefRegelInfo(
                categorie=         fr.categorie,
                uren=              fr.uren,
                tarief_factuur=    fr.tarief,
                bedrag=            fr.bedrag,
                loonschaal=        match.loonschaal,
                contractvorm=      match.contractvorm,
                tarief_in_tabel=   match.tarief_in_tabel,
                tarief_delta=      match.delta,
                binnen_tolerantie= match.binnen_tolerantie,
            ))
        else:
            r.tarief_regels.append(TariefRegelInfo(
                categorie=         fr.categorie,
                uren=              fr.uren,
                tarief_factuur=    fr.tarief,
                bedrag=            fr.bedrag,
                binnen_tolerantie= False,
            ))


def samenvat(resultaten: list[MatchResultaat]) -> dict:
    """Geef telling per status + financiële impact."""
    tellingen = defaultdict(int)
    impact_eur = 0.0
    impact_uren = 0.0
    for r in resultaten:
        tellingen[r.hoofdstatus] += 1
        if r.hoofdstatus == "niet_in_excel":
            impact_eur += r.bedrag_factuur
            impact_uren += r.uren_factuur
        elif r.hoofdstatus == "uren_afwijking" and r.is_te_veel_gefactureerd:
            # gemiddeld tarief om bedrag te schatten
            gem_tarief = (r.bedrag_factuur / r.uren_factuur) if r.uren_factuur else 0
            impact_eur += r.uren_verschil * gem_tarief
            impact_uren += r.uren_verschil
    return {
        "tellingen": dict(tellingen),
        "te_veel_gefactureerd_eur": round(impact_eur, 2),
        "te_veel_gefactureerd_uren": round(impact_uren, 2),
        "totaal": len(resultaten),
    }
