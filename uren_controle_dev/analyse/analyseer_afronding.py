"""Analyseer welke afrondingsregel Nitea toepast.

Voor elke regel in de Nitea PDF:
  - bereken bruto = (eindtijd - begintijd - pauze)
  - vergelijk met Nitea's "Werk tijd"
  - registreer het verschil in minuten

Dat geeft inzicht in hoe Nitea afrondt op kwartieren.
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

import pdfplumber

NITEA_PDF = Path("C:/Users/dieter.KWEKERIJBAAS/Downloads/WK 15 L1 Nitea overzicht.pdf")

# Regex per data-regel: <volgnr> <nr> - <naam> <dd-mm-yyyy> <begin> <eind> <werk> <pauze?>
DATA_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)\s*-\s*(.+?)\s+"
    r"(\d{2}-\d{2}-\d{4})\s+"
    r"(\d{1,2}:\d{2})\s+"
    r"(\d{1,2}:\d{2})\s+"
    r"(\d{1,2}:\d{2})"
    r"(?:\s+(\d{1,2}:\d{2}))?\s*$"
)


def hhmm_naar_minuten(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def minuten_naar_hhmm(m: int) -> str:
    teken = "-" if m < 0 else ""
    m = abs(m)
    return f"{teken}{m // 60}:{m % 60:02d}"


def parse_nitea(pdf_pad: Path) -> list[dict]:
    regels: list[dict] = []
    with pdfplumber.open(pdf_pad) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                m = DATA_RE.match(line)
                if not m:
                    continue
                _, nr, naam, datum, begin, eind, werk, pauze = m.groups()
                regels.append({
                    "nr":     nr,
                    "naam":   naam.strip(),
                    "datum":  datum,
                    "begin":  begin,
                    "eind":   eind,
                    "werk":   werk,            # afgerond door Nitea
                    "pauze":  pauze or "0:00",
                })
    return regels


def analyseer(regels: list[dict]) -> None:
    print(f"Nitea-regels totaal: {len(regels)}\n")

    bruto_min_per_regel: list[int] = []
    afgerond_min_per_regel: list[int] = []
    correcties: list[tuple[int, dict]] = []   # (delta_min, regel)

    for r in regels:
        bruto_min = (
            hhmm_naar_minuten(r["eind"])
            - hhmm_naar_minuten(r["begin"])
            - hhmm_naar_minuten(r["pauze"])
        )
        # Werktijd kan negatief zijn als shift over middernacht loopt
        # — niet in deze data, maar laten we eerlijk omgaan
        if bruto_min < 0:
            bruto_min += 24 * 60
        afgerond_min = hhmm_naar_minuten(r["werk"])
        delta = afgerond_min - bruto_min      # + = naar boven, - = naar beneden
        bruto_min_per_regel.append(bruto_min)
        afgerond_min_per_regel.append(afgerond_min)
        correcties.append((delta, r))

    # ── 1. Globale verdeling van delta's ──────────────────────────────────
    counter: Counter = Counter()
    for delta, _ in correcties:
        counter[delta] += 1

    print("=== Verdeling delta's (afgerond - bruto) in minuten ===")
    for delta in sorted(counter):
        bar = "█" * min(counter[delta], 60)
        print(f"  {delta:+4d} min : {counter[delta]:4d}  {bar}")

    # ── 2. Bruto-minuten modulo 15 → afronding-richting ───────────────────
    print()
    print("=== Bruto-restant modulo 15 → afronding-richting ===")
    print("(als regel 'tot en met 12 min naar beneden, vanaf 13 naar boven' klopt,")
    print(" dan zou voor restanten 0-12 de delta ≤ 0 zijn, en voor 13-14 de delta > 0)")
    print()
    rest_dist: dict[int, Counter] = defaultdict(Counter)
    for delta, r in correcties:
        bruto_min = (
            hhmm_naar_minuten(r["eind"])
            - hhmm_naar_minuten(r["begin"])
            - hhmm_naar_minuten(r["pauze"])
        )
        rest = bruto_min % 15
        rest_dist[rest][delta] += 1

    for rest in sorted(rest_dist):
        print(f"  bruto % 15 = {rest:2d} ({len(list(rest_dist[rest].elements())):3d} regels):")
        for delta in sorted(rest_dist[rest]):
            print(f"      delta {delta:+3d} min : {rest_dist[rest][delta]} keer")

    # ── 3. Voorbeelden van naar-boven afrondingen (delta > 0) ─────────────
    omhoog = [(d, r) for d, r in correcties if d > 0]
    omlaag = [(d, r) for d, r in correcties if d < 0]
    gelijk = [(d, r) for d, r in correcties if d == 0]
    print()
    print(f"=== Samenvatting ===")
    print(f"  exact (delta=0)   : {len(gelijk):4d}")
    print(f"  naar beneden (-)  : {len(omlaag):4d}")
    print(f"  naar boven (+)    : {len(omhoog):4d}")

    if omhoog:
        print()
        print("=== Voorbeelden naar-boven afronding ===")
        for d, r in sorted(omhoog, key=lambda x: -x[0])[:10]:
            print(f"  +{d:2d} min: {r['naam']:30s} {r['datum']} "
                  f"{r['begin']}-{r['eind']} pauze {r['pauze']:5s} "
                  f"→ Nitea {r['werk']}")

    print()
    print("=== Voorbeelden naar-beneden afronding (top 15) ===")
    for d, r in sorted(omlaag, key=lambda x: x[0])[:15]:
        print(f"  {d:3d} min: {r['naam']:30s} {r['datum']} "
              f"{r['begin']}-{r['eind']} pauze {r['pauze']:5s} "
              f"→ Nitea {r['werk']}")


if __name__ == "__main__":
    if not NITEA_PDF.exists():
        sys.exit(f"Niet gevonden: {NITEA_PDF}")
    regels = parse_nitea(NITEA_PDF)
    analyseer(regels)
