"""Naam-matching: factuur-naam (LO format) ↔ doorgegeven-uren-naam (Kwekerij format).

Voorbeelden van LO factuur-namen:
    "K. Adamovych (Karyna)"           → achternaam Adamovych, voornaam Karyna
    "I.A. Anghel (Ionut)"             → achternaam Anghel, voornaam Ionut
    "I.A. Baldowska (Iwona)"          → achternaam Baldowska, voornaam Iwona
    "L.S. Baldowski (Lukasz)"         → achternaam Baldowski, voornaam Lukasz
    "P.M. Kolodziej (Patryk)"         → achternaam Kolodziej, voornaam Patryk

Voorbeelden Excel-namen:
    "Marius Mic", "Karyna Adamovych", "Iwona Baldowska", "Patryk Kolodziej"

Strategie:
1. Parse factuur-naam: voornaam tussen () en achternaam direct vóór ()
2. Match op (achternaam, voornaam) — case-insensitive, leestekens weg
3. Bij meerdere matches: kies degene wiens initialen overeenkomen met de initialen-prefix
4. Bij geen exacte match: fuzzy match op (achternaam, voornaam) met SequenceMatcher
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional


# ── helpers ─────────────────────────────────────────────────────────────────
def _normaliseer(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower().strip())


@dataclass
class FactuurNaamComponenten:
    raw:        str           # zoals op factuur ("I.A. Baldowska (Iwona)")
    initialen:  list[str]     # ["I", "A"]
    achternaam: str           # "Baldowska"
    voornaam:   str           # "Iwona"  — uit haakjes


def parse_factuurnaam(naam: str) -> FactuurNaamComponenten:
    """Splits een LO-factuurnaam in initialen, achternaam en voornaam."""
    raw = naam.strip()

    # Voornaam tussen haakjes
    m = re.search(r"\(([^)]+)\)\s*$", raw)
    voornaam = m.group(1).strip() if m else ""

    # Strip de haakjes om de hoofdtekst te krijgen
    hoofd = re.sub(r"\s*\([^)]+\)\s*$", "", raw).strip()

    # Initialen aan begin van hoofdtekst
    initialen = re.findall(r"([A-Z])\.", hoofd)

    # Achternaam = laatste woord van hoofdtekst
    parts = re.sub(r"^(?:[A-Z]\.)+\s*", "", hoofd).split()
    achternaam = parts[-1] if parts else hoofd

    return FactuurNaamComponenten(
        raw=raw,
        initialen=initialen,
        achternaam=achternaam,
        voornaam=voornaam,
    )


# ── publieke API ────────────────────────────────────────────────────────────
def match_naam(naam_factuur: str, kandidaten: list, fuzzy_drempel: float = 0.85):
    """Zoek de best passende DoorgegevenRegel voor een factuurnaam.

    `kandidaten` zijn DoorgegevenRegel objecten met velden voornaam / achternaam.
    Retourneert (regel, score, methode) of (None, 0.0, "") als geen match.

    methode ∈ {"exact_an_vn", "exact_an_init", "exact_an", "fuzzy"}
    """
    f = parse_factuurnaam(naam_factuur)
    fac_an = _normaliseer(f.achternaam)
    fac_vn = _normaliseer(f.voornaam)
    if not fac_an:
        return None, 0.0, ""

    # 1. Exacte achternaam-match
    op_an = [k for k in kandidaten if _normaliseer(k.achternaam) == fac_an]

    if len(op_an) == 1:
        return op_an[0], 1.0, "exact_an"

    if len(op_an) > 1:
        # 2a. Disambigueer op voornaam (volledig)
        if fac_vn:
            op_vn = [k for k in op_an if _normaliseer(k.voornaam) == fac_vn]
            if len(op_vn) == 1:
                return op_vn[0], 1.0, "exact_an_vn"

        # 2b. Disambigueer op eerste initiaal van voornaam
        if f.initialen:
            init = f.initialen[0].upper()
            op_init = [k for k in op_an
                       if k.voornaam and k.voornaam[0].upper() == init]
            if len(op_init) == 1:
                return op_init[0], 1.0, "exact_an_init"
            if op_init:
                op_an = op_init  # smaller pool voor fuzzy

        # Nog meerdere: pak hoogste fuzzy-score op voornaam
        beste = None
        beste_score = 0.0
        for k in op_an:
            score = SequenceMatcher(None, fac_vn, _normaliseer(k.voornaam)).ratio()
            if score > beste_score:
                beste_score = score
                beste = k
        if beste:
            return beste, beste_score, "exact_an"

    # 3. Geen achternaam-match: fuzzy op achternaam
    beste = None
    beste_score = 0.0
    for k in kandidaten:
        an_score = SequenceMatcher(None, fac_an, _normaliseer(k.achternaam)).ratio()
        # Combineer met voornaam-score als beschikbaar
        if fac_vn and k.voornaam:
            vn_score = SequenceMatcher(None, fac_vn, _normaliseer(k.voornaam)).ratio()
            score = (an_score * 0.7) + (vn_score * 0.3)
        else:
            score = an_score
        if score > beste_score:
            beste_score = score
            beste = k

    if beste and beste_score >= fuzzy_drempel:
        return beste, beste_score, "fuzzy"
    return None, beste_score, ""


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from parsers.excel_doorgegeven import lees_doorgegeven

    week = lees_doorgegeven(
        "C:/Users/dieter.KWEKERIJBAAS/Downloads/WK 15 L1.xlsx", "L1"
    )
    print(f"Excel: {len(week.regels)} medewerkers\n")

    # Test parse_factuurnaam
    for naam in [
        "K. Adamovych (Karyna)",
        "I.A. Anghel (Ionut)",
        "P.M. Kolodziej (Patryk)",
        "B. Baprawska (Barbara)",
        "M.M. Bielinski (Michal)",
    ]:
        c = parse_factuurnaam(naam)
        print(f"  {naam:<30s} → init={c.initialen} an={c.achternaam!r} vn={c.voornaam!r}")
    print()

    # Test match op echte data
    from parsers.factuur_l1 import lees_factuur_l1
    fac = lees_factuur_l1(
        "C:/Users/dieter.KWEKERIJBAAS/Downloads/PP_IFAC26-01896_Level One Uitzendbureau B.V._02602743_PurchaseInvoice.pdf"
    )
    namen_op_factuur = sorted({r.naam_factuur for r in fac.regels})
    print(f"Factuur 01896: {len(namen_op_factuur)} unieke namen")

    treffers = 0
    misses: list[tuple[str, float]] = []
    for nf in namen_op_factuur:
        match, score, methode = match_naam(nf, week.regels)
        if match:
            treffers += 1
        else:
            misses.append((nf, score))

    print(f"  Treffers: {treffers}/{len(namen_op_factuur)}")
    if misses:
        print(f"  Niet gematcht ({len(misses)}):")
        for nf, sc in misses:
            print(f"    {nf}  (max fuzzy {sc:.2f})")
