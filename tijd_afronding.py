"""
Centrale tijd-afronding regels voor Kwekerij Baas urencontrole.

Twee regels worden door deze module geleverd:

1. WERKTIJD-AFRONDING (Nitea):
   Werktijd wordt door Nitea ALTIJD naar beneden afgerond op kwartieren.
   Functie: rond_werktijd_naar_beneden_kwartier(werktijd_uren).

2. KLOKTIJD-AFRONDING (Baas-regel, voor toeslag-berekening):
   Begin- en eindtijden worden afgerond op het dichtstbijzijnde hele OF halve uur.
   30-min granulariteit. Bv. 5:57 → 6:00, 6:14 → 6:00, 6:15 → 6:30, 17:57 → 18:00.
   Functie: afronden_halve_uur(minuten_sinds_middernacht).

Beide functies worden centraal hier beheerd zodat élke pipeline (Nitea, SNOOP, eventueel
toekomstige Excel-imports met kloktijden) dezelfde regels gebruikt.
"""
from typing import Optional


def afronden_halve_uur(minuten: Optional[int]) -> Optional[int]:
    """Rond minuten-sinds-middernacht af op het dichtstbijzijnde hele of halve uur.

    30-minuut granulariteit. Banker's rounding op exact 15-min/45-min punten
    via Python's round() — dat is acceptabel omdat afgeronde tijden in
    arbeidscontext zelden precies halverwege zijn.

    Voorbeelden:
      5:57  (357)  → 6:00  (360)
      6:14  (374)  → 6:00  (360)
      6:15  (375)  → 6:30  (390)  [Banker's: kan ook 6:00 zijn, getest = 6:30]
      6:44  (404)  → 6:30  (390)
      6:45  (405)  → 7:00  (420)
      7:30  (450)  → 7:30  (450)
      17:57 (1077) → 18:00 (1080)

    None in → None uit (voor lege begin-/eindtijden).
    """
    if minuten is None:
        return None
    # Half-up rounding (bij exact halverwege → naar boven, niet banker's)
    # 6:15 (375) → 6:30 (390): (375 + 15) // 30 = 13, 13 * 30 = 390
    return ((minuten + 15) // 30) * 30


def rond_werktijd_naar_beneden_kwartier(werktijd_uren: float) -> float:
    """Rond werktijd in uren naar beneden af op kwartier (15 min = 0.25h).

    Nitea-standaard afronding. Voorbeelden:
      10.85 (10:51) → 10.75 (10:45)
      8.16  (8:10)  → 8.00  (8:00)
      9.45  (9:27)  → 9.25  (9:15)
    """
    return int(werktijd_uren * 4) / 4.0


def hhmm_naar_minuten(hhmm: str) -> Optional[int]:
    """Parse 'HH:MM' naar minuten sinds middernacht. Lege string → None."""
    s = (hhmm or '').strip()
    if not s or ':' not in s:
        return None
    try:
        h, m = s.split(':')
        return int(h) * 60 + int(m)
    except Exception:
        return None


def minuten_naar_hhmm(minuten: Optional[int]) -> str:
    """Inverse van hhmm_naar_minuten. None of fout → ''."""
    if minuten is None:
        return ''
    h, m = divmod(int(minuten), 60)
    return f'{h:d}:{m:02d}'


# ============================================================
# Smoke tests (run als module direct uitgevoerd wordt)
# ============================================================
if __name__ == '__main__':
    cases = [
        # (klok, verwachte afronding)
        ('5:57', '6:00'),
        ('5:56', '6:00'),
        ('5:30', '5:30'),
        ('5:29', '5:30'),
        ('5:14', '5:00'),
        ('6:00', '6:00'),
        ('6:14', '6:00'),
        ('6:15', '6:30'),
        ('6:44', '6:30'),
        ('6:45', '7:00'),
        ('7:30', '7:30'),
        ('17:57', '18:00'),
        ('18:00', '18:00'),
        ('18:08', '18:00'),
        ('18:15', '18:30'),
    ]
    print('Klok-tijd afronding:')
    fails = 0
    for klok, verwacht in cases:
        result = minuten_naar_hhmm(afronden_halve_uur(hhmm_naar_minuten(klok)))
        ok = 'OK ' if result == verwacht else 'FAIL'
        if result != verwacht:
            fails += 1
        print(f'  {ok}  {klok:>6} -> {result:>6}  (verwacht {verwacht})')
    print()
    print('Werktijd kwartier-afronding (naar beneden):')
    werktijd_cases = [(10.85, 10.75), (8.16, 8.00), (9.45, 9.25), (10.0, 10.0), (10.24, 10.0)]
    for werktijd, verwacht in werktijd_cases:
        result = rond_werktijd_naar_beneden_kwartier(werktijd)
        ok = 'OK ' if abs(result - verwacht) < 0.001 else 'FAIL'
        if abs(result - verwacht) >= 0.001:
            fails += 1
        print(f'  {ok}  {werktijd:>5.2f}h -> {result:>5.2f}h  (verwacht {verwacht})')
    print()
    print(f'Resultaat: {len(cases) + len(werktijd_cases) - fails}/{len(cases) + len(werktijd_cases)} OK')
