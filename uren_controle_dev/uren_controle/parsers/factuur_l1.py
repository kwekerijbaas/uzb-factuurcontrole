"""Parser voor Level One Uitzendbureau B.V. PDF-facturen.

Format (gebaseerd op week 15 2026):
  - Header per pagina: Kwekerij Baas adres + tabelhoofd
  - Per medewerker een blok:
        Week: 2026-15
        Naam: K. Adamovych (Karyna)
        Loon normale uren            8:45  28,942  253,23
        Loon overwerkuren 135,00%    6:30  28,942  188,11
        Loon overwerkuren 150,00%    9:51  33,922  381,69
        [Loon overwerkuren 200,00%   X:XX  YY,YYY  ZZ,ZZ]   (optioneel)
        [Loon bijzondere uren 150,00% ...]                  (optioneel)
        [Bereikbaarheidstoeslag ...]                        (optioneel — los uurtarief)
        [Reiskostenvergoeding ...]                          (optioneel)
  - Pagina-voet: "Factuurnummer XXXXXXXX. Transporteren pagina N <subtotaal>"
  - Laatste pagina: "Totaal NN:MM <bedrag>" + BTW + factuurbedrag
  - Onder: "Relatie XXXXXX  Factuur XXXXXXXX  Datum DD-MM-YYYY € <bedrag>"

Output: FactuurL1 met FactuurRegelL1 per (medewerker, categorie).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber


# Categorie-detectie. Volgorde matters: meer specifieke regex eerst.
CATEGORIE_PATRONEN: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^Loon\s+normale\s+uren\b", re.I),                   "100"),
    (re.compile(r"^Loon\s+overwerkuren\s+135[,\.]?00?%", re.I),       "135"),
    (re.compile(r"^Loon\s+overwerkuren\s+150[,\.]?00?%", re.I),       "150"),
    (re.compile(r"^Loon\s+overwerkuren\s+200[,\.]?00?%", re.I),       "200"),
    (re.compile(r"^Loon\s+bijzondere\s+uren\s+150[,\.]?00?%", re.I),  "bijz_150"),
    (re.compile(r"^Bereikbaarheidstoeslag", re.I),                    "bereikbaarheid"),
    (re.compile(r"^Reiskostenvergoeding", re.I),                      "reiskosten"),
    (re.compile(r"^Correctie\s+van\s+factuur", re.I),                 "correctie"),
]

# Cijfer-extractie aan einde van een regel
NUMBER_RE = re.compile(
    r"(?P<uren>\d{1,3}:\d{2})\s+"
    r"(?P<tarief>\d{1,3}[,\.]\d{2,3})\s+"
    r"(?P<bedrag>-?\d{1,3}(?:\.\d{3})*[,\.]\d{2})"
)
# Voor regels zonder uren-kolom (bv. reiskosten in € per stuk):
NUMBER_GEEN_UREN_RE = re.compile(
    r"(?P<eenheden>\d+(?:[,\.]\d+)?)\s+"
    r"(?P<tarief>\d{1,3}[,\.]\d{2,3})\s+"
    r"(?P<bedrag>-?\d{1,3}(?:\.\d{3})*[,\.]\d{2})"
)

WEEK_RE = re.compile(r"^Week:\s*(\d{4})-(\d{1,2})\s*$", re.I)
NAAM_RE = re.compile(r"^Naam:\s*(.+?)\s*$", re.I)
TOTAAL_RE = re.compile(r"^Totaal\s+(\d+:\d{2})\s+([\d\.]+,\d{2})\s*$", re.I)
# Header van de factuurmeta-tabel
RELATIE_HDR_RE = re.compile(r"^Relatie\s+Factuur\s+Datum\s+Factuurbedrag", re.I)
# Waarden-regel onder die header: "200006 02602745 17-04-2026€ 2.093,11"
RELATIE_WAARDEN_RE = re.compile(
    r"^(\d+)\s+(\d+)\s+(\d{2}-\d{2}-\d{4})\s*€?\s*(\d{1,3}(?:\.\d{3})*,\d{2})"
)
# Fallback: "Relatie X Factuur Y Datum Z" direct op één regel (oud format)
RELATIE_INLINE_RE = re.compile(
    r"Relatie\s+(\d+)\s+Factuur\s+(\d+)\s+Datum\s+(\d{2}-\d{2}-\d{4})", re.I
)
FACTUURBEDRAG_RE = re.compile(r"€\s*(\d{1,3}(?:\.\d{3})*,\d{2})\s*$")


@dataclass
class FactuurRegelL1:
    naam_factuur: str          # zoals exact op factuur ("K. Adamovych (Karyna)")
    week_nr:      int
    jaar:         int
    categorie:    str          # 100 / 135 / 150 / 200 / bijz_150 / bereikbaarheid / reiskosten / correctie
    uren:         float        # 0.0 als niet van toepassing (reiskosten, bereikbaarheid)
    eenheden:     float = 0.0  # voor non-uur regels (bv. km, dagen)
    tarief:       float = 0.0
    bedrag:       float = 0.0
    raw_omschrijving: str = ""


@dataclass
class FactuurL1:
    bestandsnaam:  str
    factuurnummer: str = ""
    relatie:       str = ""
    datum:         str = ""
    week_nr:       int = 0
    jaar:          int = 0
    factuurbedrag: float = 0.0     # incl BTW
    netto_bedrag:  float = 0.0     # excl BTW (laatste 'Totaal' regel)
    netto_uren:    float = 0.0     # som uren over alle regels (excl supplementen)
    regels:        list[FactuurRegelL1] = field(default_factory=list)


# ── helpers ─────────────────────────────────────────────────────────────────
def _hhmm_naar_uren(s: str) -> float:
    h, m = s.split(":")
    return int(h) + int(m) / 60


def _eu_naar_float(s: str) -> float:
    """1.234,56 → 1234.56 ;  28,942 → 28.942"""
    s = s.replace(".", "").replace(",", ".")
    return float(s)


def _is_pagina_kop(line: str) -> bool:
    return any(x in line for x in (
        "A. Baas Pot- en Tuinplantenkwekerij", "Enserweg 4", "8307 PL ENS",
        "Factuur", "Omschrijving Uren/eenh.",
        "Getransporteerd van pagina", "Transporteren pagina",
        "Wilt u bij betaling", "BTW-identificatienummer",
        "Vestigingsadres", "incassomachtiging", "BTW over",
        "automatisch van uw bankrekening",
    ))


def _detecteer_categorie(omschrijving: str) -> str | None:
    for pat, code in CATEGORIE_PATRONEN:
        if pat.search(omschrijving):
            return code
    return None


# ── lichtgewicht detectie ──────────────────────────────────────────────────
def detecteer_week_jaar(pdf_pad: str | Path) -> tuple[int, int]:
    """Snelle helper: lees alleen pagina 1 van de PDF om (week_nr, jaar) te vinden.

    Veel sneller dan `lees_factuur_l1` omdat alleen één pagina wordt geparseerd
    en geen factuurregels worden geëxtraheerd. Bedoeld voor inventarisatie van
    de Downloads-map.

    Retourneert (0, 0) als niet detecteerbaar.
    """
    pad = Path(pdf_pad)
    with pdfplumber.open(pad) as pdf:
        if not pdf.pages:
            return 0, 0
        text = pdf.pages[0].extract_text() or ""
    for line in text.split("\n"):
        m = WEEK_RE.match(line.strip())
        if m:
            return int(m.group(2)), int(m.group(1))
    return 0, 0


# ── parser ──────────────────────────────────────────────────────────────────
def lees_factuur_l1(pdf_pad: str | Path) -> FactuurL1:
    pad = Path(pdf_pad)
    factuur = FactuurL1(bestandsnaam=pad.name)

    with pdfplumber.open(pad) as pdf:
        alle_regels: list[str] = []
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                line = line.rstrip()
                if line.strip():
                    alle_regels.append(line)

    huidige_naam = ""
    huidige_week = 0
    huidig_jaar = 0
    relatie_header_gezien = False

    for line in alle_regels:
        # Week + jaar
        m = WEEK_RE.match(line)
        if m:
            huidig_jaar = int(m.group(1))
            huidige_week = int(m.group(2))
            if not factuur.week_nr:
                factuur.week_nr = huidige_week
                factuur.jaar = huidig_jaar
            continue

        # Naam
        m = NAAM_RE.match(line)
        if m:
            huidige_naam = m.group(1).strip()
            continue

        # Totaalregel onderaan factuur
        m = TOTAAL_RE.match(line)
        if m:
            factuur.netto_uren = round(_hhmm_naar_uren(m.group(1)), 2)
            factuur.netto_bedrag = _eu_naar_float(m.group(2))
            continue

        # Relatie/Factuur header (zonder waarden) op één regel
        if RELATIE_HDR_RE.match(line):
            relatie_header_gezien = True
            continue

        # Relatie/factuur waarden — direct na de header
        if relatie_header_gezien:
            m = RELATIE_WAARDEN_RE.match(line)
            if m:
                factuur.relatie       = m.group(1)
                factuur.factuurnummer = m.group(2)
                factuur.datum         = m.group(3)
                factuur.factuurbedrag = _eu_naar_float(m.group(4))
                relatie_header_gezien = False
                continue

        # Inline (oud) format
        m = RELATIE_INLINE_RE.search(line)
        if m and not factuur.factuurnummer:
            factuur.relatie       = m.group(1)
            factuur.factuurnummer = m.group(2)
            factuur.datum         = m.group(3)
            mb = FACTUURBEDRAG_RE.search(line)
            if mb:
                factuur.factuurbedrag = _eu_naar_float(mb.group(1))
            continue

        # Categorie-regel?
        cat = _detecteer_categorie(line)
        if cat is None:
            continue

        # Probeer eerst HH:MM-format
        m = NUMBER_RE.search(line)
        if m:
            uren = _hhmm_naar_uren(m.group("uren"))
            tarief = _eu_naar_float(m.group("tarief"))
            bedrag = _eu_naar_float(m.group("bedrag"))
            factuur.regels.append(FactuurRegelL1(
                naam_factuur=     huidige_naam,
                week_nr=          huidige_week,
                jaar=             huidig_jaar,
                categorie=        cat,
                uren=             uren,
                tarief=           tarief,
                bedrag=           bedrag,
                raw_omschrijving= line,
            ))
            continue

        # Geen HH:MM — probeer numeriek (bv. reiskosten/dagen)
        m = NUMBER_GEEN_UREN_RE.search(line)
        if m:
            eenheden = _eu_naar_float(m.group("eenheden"))
            tarief = _eu_naar_float(m.group("tarief"))
            bedrag = _eu_naar_float(m.group("bedrag"))
            factuur.regels.append(FactuurRegelL1(
                naam_factuur=     huidige_naam,
                week_nr=          huidige_week,
                jaar=             huidig_jaar,
                categorie=        cat,
                uren=             0.0,
                eenheden=         eenheden,
                tarief=           tarief,
                bedrag=           bedrag,
                raw_omschrijving= line,
            ))

    return factuur


def som_uren_per_naam(factuur: FactuurL1) -> dict:
    """Geef per medewerker totaal uren (uren-categorieen, exclusief reiskosten/bereikbaarheid)."""
    UREN_CATS = {"100", "135", "150", "200", "bijz_150", "correctie"}
    uit: dict[str, float] = {}
    for r in factuur.regels:
        if r.categorie in UREN_CATS:
            uit[r.naam_factuur] = uit.get(r.naam_factuur, 0.0) + r.uren
    return {n: round(u, 2) for n, u in uit.items()}


def som_bedrag_per_naam(factuur: FactuurL1) -> dict:
    """Geef per medewerker totaalbedrag (alle categorieën)."""
    uit: dict[str, float] = {}
    for r in factuur.regels:
        uit[r.naam_factuur] = uit.get(r.naam_factuur, 0.0) + r.bedrag
    return {n: round(b, 2) for n, b in uit.items()}


if __name__ == "__main__":
    import sys
    paden = [
        "C:/Users/dieter.KWEKERIJBAAS/Downloads/PP_IFAC26-01877_Level One Uitzendbureau B.V._02602745_PurchaseInvoice.pdf",
        "C:/Users/dieter.KWEKERIJBAAS/Downloads/PP_IFAC26-01878_Level One Uitzendbureau B.V._02602744_PurchaseInvoice.pdf",
        "C:/Users/dieter.KWEKERIJBAAS/Downloads/PP_IFAC26-01896_Level One Uitzendbureau B.V._02602743_PurchaseInvoice.pdf",
    ]
    totaal_factuur_uren = 0.0
    totaal_factuur_bedrag = 0.0
    for p in paden:
        f = lees_factuur_l1(p)
        print(f"\n=== {Path(p).name} ===")
        print(f"  Factuur {f.factuurnummer} | Week {f.week_nr}/{f.jaar} | datum {f.datum}")
        print(f"  Netto: {f.netto_uren}u  €{f.netto_bedrag:,.2f}".replace(",", "_").replace(".", ",").replace("_", "."))
        print(f"  Factuurbedrag (incl BTW): €{f.factuurbedrag:,.2f}".replace(",", "_").replace(".", ",").replace("_", "."))
        per_naam = som_uren_per_naam(f)
        print(f"  Medewerkers: {len(per_naam)}, regels: {len(f.regels)}")
        # Eerste 3 medewerkers
        for nm in list(per_naam)[:3]:
            cat_sum = {}
            for r in f.regels:
                if r.naam_factuur == nm:
                    cat_sum[r.categorie] = cat_sum.get(r.categorie, 0.0) + r.uren
            print(f"    {nm:35s} {per_naam[nm]:6.2f}u  {cat_sum}")
        totaal_factuur_uren += f.netto_uren
        totaal_factuur_bedrag += f.netto_bedrag

    print(f"\n=== Totaal over {len(paden)} facturen ===")
    print(f"  Uren  : {totaal_factuur_uren:.2f}")
    bedrag_str = f"€{totaal_factuur_bedrag:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    print(f"  Bedrag (excl BTW): {bedrag_str}")
