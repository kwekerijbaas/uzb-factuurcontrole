"""
Toeslagcontrole: leest een Nitea-overzicht-PDF (werkelijke start/eindtijden per
medewerker per dag) en berekent de VERWACHTE toeslag-uren per medewerker volgens
de Glastuinbouw-CAO + Baas-regels. Wordt vergeleken met de factuur-toeslag.

CAO/Baas-regels (zie memory project_uren_controle):
  - Jaarurenmodel normweek 38u: 0-38 = 100%, 38-48 = 135% (OT), >48 of >10u/dag = 150%.
  - Nacht (Sub III): Ma-Vr 20:00-06:00 = +50% nacht-supplement.
  - Za 00-06 + 15-24 = +50% (za-supplement); Zo = +100%.
  - Nachtdienst (geen begin/eind in Nitea, of eind<begin): aanname shift 18:00-06:00.
  - Begin/eindtijd afronden op heel/half uur (tijd_afronding.afronden_halve_uur).
"""
import re
import sys
from datetime import datetime, date, timedelta
import pdfplumber

sys.path.insert(0, r'C:\Users\dieter.KWEKERIJBAAS\Kwekerij Baas\Directie - Projecten')
from tijd_afronding import afronden_halve_uur

NORMWEEK = 38.0
OW_50_GRENS = 48.0
MAX_DAG = 10.0


def _min(s):
    s = (s or '').strip()
    if not s or ':' not in s:
        return None
    h, m = s.split(':')
    return int(h) * 60 + int(m)


def _uur(s):
    s = (s or '').strip()
    if ':' not in s:
        return 0.0
    h, m = s.split(':')
    return int(h) + int(m) / 60.0


def _norm(n):
    return re.sub(r'\s+', ' ', str(n).strip().lower())


def parse_nitea(pdf_path):
    """Return (week_start_date, {naam_norm: {'tag':.., 'naam':.., 'dagen':{datum:info}}})."""
    with pdfplumber.open(pdf_path) as pdf:
        text = '\n'.join((p.extract_text() or '') for p in pdf.pages)
    med = {}
    data_min = None
    for ln in text.split('\n'):
        ln = ln.strip()
        m = re.match(r'^\d+\s+(\d+)\s+-\s+(.+?)\s+(\d{2}-\d{2}-\d{4})\s+(.*)$', ln)
        if not m:
            continue
        tag, naam, datum_s, rest = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
        toks = re.findall(r'\d+:\d{2}', rest)
        begin = eind = None
        werk = pauze = 0.0
        if len(toks) == 4:
            begin, eind, werk, pauze = _min(toks[0]), _min(toks[1]), _uur(toks[2]), _uur(toks[3])
        elif len(toks) == 3:
            t1 = _min(toks[0])
            if t1 is not None and t1 >= 12 * 60:
                begin = t1
            else:
                eind = t1
            werk, pauze = _uur(toks[1]), _uur(toks[2])
        elif len(toks) == 2:
            werk, pauze = _uur(toks[0]), _uur(toks[1])
        else:
            continue
        d = datetime.strptime(datum_s, '%d-%m-%Y').date()
        data_min = d if data_min is None else min(data_min, d)
        norm = _norm(naam)
        rec = med.setdefault(norm, {'tag': tag, 'naam': naam, 'dagen': {}})
        rec['dagen'][d] = {'begin': begin, 'eind': eind, 'werk': werk, 'pauze': pauze}
    week_start = data_min - timedelta(days=data_min.weekday()) if data_min else None
    return week_start, med


def _is_nachtdienst(dagen):
    for info in dagen.values():
        if info['werk'] > 0:
            if info['begin'] is None and info['eind'] is None:
                return True
            if info['begin'] is not None and info['eind'] is not None and info['eind'] < info['begin']:
                return True
            if info['begin'] is not None and info['begin'] >= 18 * 60:
                return True
    return False


def _nacht_uren_dag(begin, eind, weekdag, werk):
    """Uren netto in nacht-window. Ma-Vr: 00-06 + 20-24. Za: 00-06 + 15-24."""
    if begin is None or eind is None or weekdag > 5:
        return 0.0
    b = afronden_halve_uur(begin)
    e = afronden_halve_uur(eind)
    e_eff = e if e > b else e + 24 * 60
    if weekdag <= 4:
        wins = [(0, 360), (1200, 1440)]
    else:
        wins = [(0, 360), (900, 1440)]
    ov = 0
    for ws, we in wins:
        ov += max(0, min(e_eff, 1440) - max(b, ws)) if we == 1440 else max(0, min(e_eff, we) - max(b, ws))
        if e_eff > 1440:
            ov += max(0, min(e_eff - 1440, we) - max(0, ws))
    bruto = (e_eff - b) / 60.0
    return (ov / 60.0 / bruto) * werk if bruto else 0.0


def bereken_toeslag(dagen, week_start):
    """Return dict met verwachte toeslag-uren-categorieën."""
    is_nacht = _is_nachtdienst(dagen)
    dag_uren = [0.0] * 7
    nacht = 0.0
    za = zo = 0.0
    for d, info in dagen.items():
        wd = (d - week_start).days if week_start else d.weekday()
        if not 0 <= wd <= 6:
            wd = d.weekday()
        w = info['werk']
        dag_uren[wd] += w
        if is_nacht:
            # hele 18-06 shift: 18-20 dag (2u), 20-06 nacht; pauze in nacht-deel
            if wd <= 4:
                if info['begin'] is None and info['eind'] is None:
                    nacht += max(0.0, w * (10.0 / 12.0))  # tussen-dag: 10/12 nacht
                else:
                    nacht += max(0.0, min(w, w))  # randdag: benader via window
                    nacht_dag = _nacht_uren_dag(info['begin'], info['eind'], wd, w) if (info['begin'] and info['eind']) else w
                    nacht = nacht - w + nacht_dag
            elif wd == 5:
                za += w
            else:
                zo += w
        else:
            if wd == 5:
                za += w
            elif wd == 6:
                zo += w
            else:
                nacht += _nacht_uren_dag(info['begin'], info['eind'], wd, w)
    weekdag = dag_uren[:5]
    dag_ow = sum(max(0, u - MAX_DAG) for u in weekdag)
    gecorr = sum(min(u, MAX_DAG) for u in weekdag)
    if gecorr <= NORMWEEK:
        c100, c135, c150 = gecorr, 0.0, 0.0
    elif gecorr <= OW_50_GRENS:
        c100, c135, c150 = NORMWEEK, gecorr - NORMWEEK, 0.0
    else:
        c100, c135, c150 = NORMWEEK, OW_50_GRENS - NORMWEEK, gecorr - OW_50_GRENS
    c150 += dag_ow
    return {
        'totaal': round(sum(dag_uren), 2), 'is_nachtdienst': is_nacht,
        'c100': round(c100, 2), 'c135': round(c135, 2), 'c150': round(c150, 2),
        'nacht': round(nacht, 2), 'za': round(za, 2), 'zo': round(zo, 2),
    }


def toeslag_per_medewerker(pdf_path):
    """Return {naam_norm: {tag, naam, ...toeslag...}}."""
    week_start, med = parse_nitea(pdf_path)
    out = {}
    for norm, rec in med.items():
        t = bereken_toeslag(rec['dagen'], week_start)
        t['tag'] = rec['tag']
        t['naam'] = rec['naam']
        out[norm] = t
    return out


if __name__ == '__main__':
    import os
    p = sys.argv[1]
    res = toeslag_per_medewerker(p)
    print(f'{os.path.basename(p)}: {len(res)} medewerkers')
    for norm, t in list(res.items())[:6]:
        mark = ' [NACHT]' if t['is_nachtdienst'] else ''
        print(f"  {t['tag']:>4} {t['naam'][:26]:<26} tot={t['totaal']:>6.2f} 100={t['c100']:>5.1f} "
              f"135={t['c135']:>5.2f} 150={t['c150']:>5.2f} nacht={t['nacht']:>5.2f} za={t['za']:.1f}{mark}")
