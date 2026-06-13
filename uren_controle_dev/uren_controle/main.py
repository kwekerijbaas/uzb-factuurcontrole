#!/usr/bin/env python3
"""Uren-controle CLI — Level One Uitzendbureau (Fase 1).

Standaard gebruik:
    python main.py
        → detecteert week + jaar uit ~/Downloads/WK <nr> L1.xlsx
        → leest alle bijbehorende LO-factuur-PDFs uit ~/Downloads/
        → schrijft rapport + mail-concept naar ~/Downloads/

Argumenten (optioneel):
    --week 15       expliciet weeknummer
    --jaar 2026     expliciet jaar
    --invoer ~/Downloads
    --uitvoer ~/Downloads
    --uzb L1        UZB-code (alleen L1 wordt nu ondersteund)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from parsers.excel_doorgegeven import (                                    # noqa: E402
    lees_doorgegeven,
    detecteer_week_jaar as detecteer_excel_week_jaar,
)
from parsers.factuur_l1 import (                                           # noqa: E402
    lees_factuur_l1,
    detecteer_week_jaar as detecteer_factuur_week_jaar,
)
from parsers.tarieven import TarievenDatabase                             # noqa: E402
from matching.valideer import valideer_week, samenvat                     # noqa: E402
from output.rapport_excel import bouw_rapport                             # noqa: E402
from output.mail_concept import bouw_mail                                 # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────
def laad_config() -> dict:
    pad = Path(__file__).parent / "config" / "drempels.yaml"
    with open(pad, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def vind_excels(invoer_map: Path, uzb: str) -> list[Path]:
    """Zoek alle WK <nr> <UZB>.xlsx in invoer_map (zonder verdere filtering)."""
    patroon = re.compile(rf"WK\s*\d+\s+{uzb}\.xlsx?$", re.I)
    return sorted(p for p in invoer_map.glob(f"WK *{uzb}.xls*") if patroon.search(p.name))


def vind_facturen(invoer_map: Path, uzb: str) -> list[Path]:
    """Zoek alle LO-factuur-PDFs (alleen Uitzendbureau, niet Payroll)."""
    if uzb != "L1":
        return []
    paden = []
    for p in invoer_map.glob("PP_IFAC*Level One Uitzendbureau B.V.*PurchaseInvoice.pdf"):
        # Sluit Payroll-facturen expliciet uit
        if "Payroll" in p.name:
            continue
        paden.append(p)
    return sorted(paden)


def inventariseer_weken(invoer_map: Path, uzb: str, log=print) -> dict:
    """Scan alle Excels en facturen in invoer_map, groepeer per (week, jaar).

    Retourneert dict {(week_nr, jaar): {'excel': Path | None, 'facturen': [Path]}}.
    Gebruikt lichtgewicht week-detectie (alleen header lezen, geen volledige parse).
    """
    inventaris: dict[tuple[int, int], dict] = {}

    for pad in vind_excels(invoer_map, uzb):
        try:
            wk, jr = detecteer_excel_week_jaar(pad)
        except Exception as e:
            log(f"  ⚠ Excel kan niet gelezen worden: {pad.name} ({e})")
            continue
        if wk == 0 or jr == 0:
            log(f"  ⚠ Geen week/jaar gedetecteerd in: {pad.name}")
            continue
        sleutel = (wk, jr)
        inventaris.setdefault(sleutel, {"excel": None, "facturen": []})
        inventaris[sleutel]["excel"] = pad

    for pad in vind_facturen(invoer_map, uzb):
        try:
            wk, jr = detecteer_factuur_week_jaar(pad)
        except Exception as e:
            log(f"  ⚠ Factuur kan niet gelezen worden: {pad.name} ({e})")
            continue
        if wk == 0 or jr == 0:
            log(f"  ⚠ Geen week/jaar gedetecteerd in: {pad.name}")
            continue
        sleutel = (wk, jr)
        inventaris.setdefault(sleutel, {"excel": None, "facturen": []})
        inventaris[sleutel]["facturen"].append(pad)

    return inventaris


def kies_week(inventaris: dict, week_filter: int = 0,
              jaar_filter: int = 0, log=print) -> tuple[int, int] | None:
    """Kies de meest recente complete week (Excel + ≥1 factuur).

    Bij `week_filter` of `jaar_filter`: filter eerst, en accepteer ook incomplete weken
    omdat de gebruiker expliciet om die week vraagt.
    """
    sleutels = sorted(inventaris.keys(), reverse=True)  # nieuwste eerst

    if week_filter or jaar_filter:
        sleutels = [
            (w, j) for (w, j) in sleutels
            if (not week_filter or w == week_filter)
            and (not jaar_filter or j == jaar_filter)
        ]
        if not sleutels:
            log(f"  ✗ Geen bestanden gevonden voor week {week_filter}/{jaar_filter}")
            return None
        return sleutels[0]

    # Geen filter — neem meest recente complete week
    for sleutel in sleutels:
        info = inventaris[sleutel]
        if info["excel"] and info["facturen"]:
            return sleutel

    # Geen complete week gevonden
    return None


def toon_inventaris(inventaris: dict, log=print) -> None:
    """Toon overzicht van alle gevonden weken — handig voor debug + transparantie."""
    if not inventaris:
        log("  (geen WK-bestanden gevonden)")
        return
    log("  Week  Excel              Facturen   Status")
    log("  ----  -----              --------   ------")
    for (wk, jr) in sorted(inventaris.keys(), reverse=True):
        info = inventaris[(wk, jr)]
        excel = "✓" if info["excel"] else "✗"
        n_fac = len(info["facturen"])
        if info["excel"] and n_fac > 0:
            status = "compleet"
        elif info["excel"]:
            status = "wacht op facturen"
        else:
            status = "alleen factuur"
        log(f"  {wk:02d}/{jr}  {excel:<18s} {n_fac:<10d} {status}")


def filter_facturen_op_week(facturen_paden: list[Path], week: int, jaar: int) -> list:
    """Lees alle facturen, filter op week + jaar, retourneer FactuurL1-objecten."""
    out = []
    for pad in facturen_paden:
        f = lees_factuur_l1(pad)
        if f.week_nr == week and f.jaar == jaar:
            out.append(f)
    return out


# ── main ───────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--week", type=int, default=0)
    p.add_argument("--jaar", type=int, default=0)
    p.add_argument("--uzb",  default="L1", help="UZB code (default L1)")
    p.add_argument("--invoer", default="~/Downloads",
                   help="Map met de Excel + PDF inputs (default ~/Downloads)")
    p.add_argument("--uitvoer", default="~/Downloads",
                   help="Map waar rapport en mail-concept terechtkomen")
    p.add_argument("--tarieven", default=None,
                   help="Pad naar tarieven_uzb.xlsx (default config/tarieven_uzb.xlsx)")
    args = p.parse_args()

    invoer = Path(os.path.expanduser(args.invoer))
    uitvoer = Path(os.path.expanduser(args.uitvoer))
    if not invoer.is_dir():
        sys.exit(f"Invoer-map bestaat niet: {invoer}")

    uzb = args.uzb.upper()
    print(f"▶ Uren-controle voor UZB {uzb}")
    print(f"  Invoer-map  : {invoer}")
    print(f"  Uitvoer-map : {uitvoer}")
    print()

    # 1. Inventariseer alles in invoer-map
    print("▶ Inventariseren ...")
    inventaris = inventariseer_weken(invoer, uzb)
    toon_inventaris(inventaris)
    print()

    keuze = kies_week(inventaris, week_filter=args.week, jaar_filter=args.jaar)
    if keuze is None:
        if args.week or args.jaar:
            sys.exit(f"✗ Geen bestanden voor week {args.week}/{args.jaar}")
        sys.exit("✗ Geen complete week gevonden (Excel + ≥1 factuur). "
                 "Plaats de juiste bestanden in de invoer-map of geef --week expliciet op.")
    week_nr, jaar = keuze
    print(f"▶ Geselecteerde week: {week_nr:02d}/{jaar}")

    excel_pad = inventaris[keuze]["excel"]
    factuur_paden = inventaris[keuze]["facturen"]

    if not excel_pad:
        sys.exit(f"✗ Geen Excel voor week {week_nr}/{jaar} — alleen facturen aanwezig.")
    if not factuur_paden:
        print(f"  ⚠ Geen factuur-PDFs voor week {week_nr}/{jaar} — controle niet mogelijk.")
        print(f"  ℹ Excel is wel aanwezig: {excel_pad.name}")
        sys.exit(0)

    print(f"  ✓ Excel: {excel_pad.name}")
    week = lees_doorgegeven(excel_pad, uzb)
    print(f"    {len(week.regels)} medewerkers, {week.totaal:.2f}u")

    # Lees alle factuur-PDFs voor deze week (volledige parse)
    facturen = []
    for pad in factuur_paden:
        f = lees_factuur_l1(pad)
        if f.week_nr == week_nr and f.jaar == jaar:
            facturen.append(f)
    if not facturen:
        sys.exit(f"✗ Geen factuur-data parseerbaar voor week {week_nr}/{jaar}")
    for f in facturen:
        print(f"    • {f.bestandsnaam}: factuur {f.factuurnummer}, "
              f"{f.netto_uren:.2f}u, €{f.netto_bedrag:,.2f}".replace(",", "_").replace(".", ",").replace("_", "."))

    # 3. Tarieven laden
    tarieven_pad = Path(args.tarieven) if args.tarieven else (
        Path(__file__).parent / "config" / "tarieven_uzb.xlsx"
    )
    if not tarieven_pad.exists():
        sys.exit(f"✗ Tarievenbestand niet gevonden: {tarieven_pad}")
    print(f"  ✓ Tarieven: {tarieven_pad.name}")
    db = TarievenDatabase(tarieven_pad)
    if uzb not in db.alle_uzbs():
        sys.exit(f"✗ Tabblad {uzb} niet aanwezig in {tarieven_pad}")

    # 4. Config laden
    config = laad_config()

    # 5. Valideren
    print()
    print("▶ Valideren ...")
    resultaten = valideer_week(week, facturen, db, config)
    sv = samenvat(resultaten)
    print(f"  Tellingen per status:")
    for status, n in sorted(sv["tellingen"].items()):
        print(f"    {status:30s}  {n}")
    if sv["te_veel_gefactureerd_eur"]:
        print(f"  ⚠ TE VEEL GEFACTUREERD: {sv['te_veel_gefactureerd_uren']}u, "
              f"€{sv['te_veel_gefactureerd_eur']:.2f}")

    # 6. Rapporten
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    rapport_naam = f"Rapportage_Urencontrole_{uzb}_WK{week_nr:02d}_{jaar}.xlsx"
    mail_naam = f"Mail_concept_{uzb}_WK{week_nr:02d}_{jaar}.txt"
    rapport_pad = uitvoer / rapport_naam
    mail_pad = uitvoer / mail_naam

    print()
    print(f"▶ Schrijven rapport: {rapport_pad}")
    bouw_rapport(resultaten, week, facturen, sv, rapport_pad)
    print(f"▶ Schrijven mail   : {mail_pad}")
    bouw_mail(resultaten, week, facturen, sv, mail_pad)

    print()
    print("✅ Klaar.")


if __name__ == "__main__":
    main()
