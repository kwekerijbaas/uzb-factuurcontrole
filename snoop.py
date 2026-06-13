"""
SNOOP-loader: medewerker -> inschaling (schaal) + geplande/werkelijke tijden.

Bron: 'snoop 1jan-26mei alle.xlsx', tab tablelist_qryomordrlne.
Kolommen: Registratienummer, Medewerker, Datum, Starttijd, Eindtijd,
          Werkelijke starttijd, Werkelijke eindtijd, Gewerkte uren, Locatie,
          Werkgever op datum, Type uitzendkracht, Tarief uitzendbureau(=inschaling).

Regels:
- Medewerkers van 'Kwekerij Baas' en 'Temper' NIET meenemen (eigen personeel).
- 'Tarief uitzendbureau' = inschaling, bv. 'B2 Sw' / 'B3 Flex' / 'F3 vast' / 'B2'.
  Leeg = nog geen tarief bekend -> bij controle UZB-tarief gebruiken met opmerking.
- Geen tag-nummer in SNOOP (Registratienummer is intern, vaak 0) -> match op naam.
"""
import re
import pandas as pd

UITGESLOTEN_WERKGEVERS = {'kwekerij baas', 'temper'}

# SNOOP 'Werkgever op datum' -> bureau-code
WERKGEVER_CODE = {
    'level one': 'L1',       # let op: kan ook Payroll zijn; schaalcode bepaalt tabel
    'sterkwerk': 'SW',
    'sterk werk': 'SW',
    'kordaat': 'CK',
    'workstead': 'CK',
}


def normalize_naam(n):
    return re.sub(r'\s+', ' ', str(n).strip().lower())


def parse_inschaling(ruw):
    """'B2 Sw' -> ('B2','SW'); 'B3 Flex' -> ('B3F','L1-flex'); 'F3 vast' -> ('F3V',..);
    'B2' -> ('B2',''); leeg -> (None,None).
    Geeft (basisschaal_genormaliseerd, suffix_info)."""
    if ruw is None or (isinstance(ruw, float) and pd.isna(ruw)):
        return None, None
    s = str(ruw).strip()
    if not s:
        return None, None
    delen = s.split()
    schaal = delen[0]
    rest = ' '.join(delen[1:]).lower() if len(delen) > 1 else ''
    if 'flex' in rest:
        return schaal + 'F', 'flex'
    if 'vast' in rest:
        return schaal + 'V', 'vast'
    if 'seizoen' in rest or rest == 's':
        return schaal + 'S', 'seizoen'
    # 'Sw'/'SW' (Sterk Werk) of geen suffix -> schaal zoals hij is
    return schaal, rest


def laad_snoop(pad):
    """Returns dict: naam_norm -> {
        'naam': str, 'bureaus': set(codes),
        'schaal': modale (meest voorkomende) basisschaal of None,
        'schaal_raw': originele inschaling-string of None,
        'heeft_tarief': bool,
        'per_datum': sorted list of (date, schaal_norm) — voor tijd-bewuste lookup.
    }
    Per_datum maakt het mogelijk om voor een factuurweek de schaal te pakken die op dat
    moment gold (handig wanneer iemand halverwege het jaar in een andere schaal komt)."""
    import pandas as _pd
    df = _pd.read_excel(pad, sheet_name='tablelist_qryomordrlne')
    df.columns = ['reg', 'naam', 'datum', 'start', 'eind', 'wstart', 'weind',
                  'uren', 'locatie', 'werkgever', 'type', 'inschaling'][:len(df.columns)]
    per = {}
    for _, r in df.iterrows():
        wg = str(r['werkgever']).strip() if pd.notna(r['werkgever']) else ''
        if wg.lower() in UITGESLOTEN_WERKGEVERS or not wg:
            continue
        naam = r['naam']
        if pd.isna(naam):
            continue
        norm = normalize_naam(naam)
        code = WERKGEVER_CODE.get(wg.lower(), wg)
        rec = per.setdefault(norm, {'naam': str(naam).strip(), 'bureaus': set(),
                                    'schaal_teller': {}, 'schaal_raw': None,
                                    'per_datum': []})
        rec['bureaus'].add(code)
        sch, _ = parse_inschaling(r['inschaling'])
        if sch:
            rec['schaal_teller'][sch] = rec['schaal_teller'].get(sch, 0) + 1
            if rec['schaal_raw'] is None:
                rec['schaal_raw'] = str(r['inschaling']).strip()
        d = r['datum']
        try:
            dt = _pd.to_datetime(d).date() if pd.notna(d) else None
        except Exception:
            dt = None
        if dt is not None:
            rec['per_datum'].append((dt, sch))
    out = {}
    for norm, rec in per.items():
        schaal = None
        if rec['schaal_teller']:
            schaal = max(rec['schaal_teller'].items(), key=lambda kv: kv[1])[0]
        per_d = sorted(rec['per_datum'], key=lambda x: x[0])
        out[norm] = {'naam': rec['naam'], 'bureaus': rec['bureaus'],
                     'schaal': schaal, 'schaal_raw': rec['schaal_raw'],
                     'heeft_tarief': schaal is not None, 'per_datum': per_d}
    return out


if __name__ == '__main__':
    import sys
    pad = sys.argv[1] if len(sys.argv) > 1 else r'C:\Users\dieter.KWEKERIJBAAS\Kwekerij Baas\Finance - Controle 2026\snoop 1jan-26mei alle.xlsx'
    d = laad_snoop(pad)
    metschaal = sum(1 for v in d.values() if v['heeft_tarief'])
    print(f'SNOOP medewerkers (excl. Kwekerij Baas/Temper): {len(d)} | met inschaling: {metschaal} | zonder: {len(d)-metschaal}')
    # voorbeelden per bureau
    import collections
    perb = collections.defaultdict(list)
    for norm, v in d.items():
        for b in v['bureaus']:
            perb[b].append(v)
    for b, lst in perb.items():
        sys.stdout.buffer.write(f'\n{b}: {len(lst)} medewerkers\n'.encode())
        for v in lst[:4]:
            sys.stdout.buffer.write(f"   {v['naam']:<28} inschaling={v['schaal_raw']} -> schaal={v['schaal']}\n".encode())
