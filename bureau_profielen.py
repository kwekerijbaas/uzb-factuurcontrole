"""
Bureau-profielen (adapters) voor de uitzendbureau-factuurcontrole.

De MOTOR (controle_uzb.py) is bureau-onafhankelijk. Per uitzendbureau is hier één
profiel dat weet:
  - hoe je herkent dat een factuur van dit bureau is        -> herkent()
  - hoe je de factuur-PDF uitleest                          -> parse_factuur()
  - waar de tarieven-/schaaltabel staat en hoe die te lezen -> laad_schaal()
  - hoe de factuurregels op de schaaltarieven mappen         -> tarief_checks()
  - welke medewerkers een aparte tariefafspraak hebben       -> techniek_tags
  - welk doorgegeven-uren-bestand bij dit bureau hoort       -> doorgegeven_hint

=====================================================================
EEN NIEUW BUREAU TOEVOEGEN (bv. Sterk Werk, Cervo Kordaat, Level One Payroll):
  1. Maak een nieuwe class die van BureauProfiel erft (kopieer het TEMPLATE onderaan).
  2. Vul herkent(), parse_factuur(), laad_schaal(), tarief_checks() in op basis van
     één voorbeeldfactuur + de tarieventabel van dat bureau.
  3. Voeg een instantie toe aan de lijst BUREAUS onderaan.
  De motor (controle_uzb.py) hoeft NIET aangepast te worden.
=====================================================================
"""
import re
import os
import glob
import pandas as pd


# ---- gedeelde helpers ----
def hhmm_to_uren(s):
    s = s.strip()
    if ':' not in s:
        return 0.0
    neg = s.startswith('-')
    s = s.lstrip('-')
    h, m = s.split(':')
    v = int(h) + int(m) / 60.0
    return -v if neg else v


def euro_to_float(s):
    s = s.strip().replace('.', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return 0.0


def komma_to_float(s):
    """'38,00' -> 38.0 ; '27,85' -> 27.85 ; trailing-minus '1.143,42-' -> -1143.42 (creditnota)."""
    s = s.strip()
    neg = s.endswith('-')
    s = s.rstrip('-').strip().replace('.', '').replace(',', '.')
    try:
        v = float(s)
        return -v if neg else v
    except ValueError:
        return 0.0


def normalize_naam(naam):
    return re.sub(r'\s+', ' ', str(naam).strip().lower())


class FactuurRegel:
    """Genormaliseerd record dat élk profiel teruggeeft per (week, medewerker)."""
    def __init__(self, week, jaar, naam_factuur, naam_norm, factuurnr):
        self.week = week
        self.jaar = jaar
        self.naam_factuur = naam_factuur
        self.naam_norm = naam_norm
        self.factuurnr = factuurnr
        self.uren_totaal = 0.0
        self.bedrag = 0.0
        self.weergeef_tarief = None     # tarief om in de detail-kolom te tonen
        # categorieën: code -> {'uren': x, 'tarief': y}
        self.categorieen = {}

    def voeg_toe(self, code, uren, tarief, bedrag, tel_uren=True):
        """tel_uren=False voor toeslag-regels (bv. SW '50% nacht') die GEEN extra
        gewerkte uren zijn maar een supplement op reeds getelde uren."""
        c = self.categorieen.setdefault(code, {'uren': 0.0, 'tarief': tarief})
        c['uren'] += uren
        c['tarief'] = tarief
        if tel_uren:
            self.uren_totaal += uren
        self.bedrag += bedrag
        if self.weergeef_tarief is None and tel_uren:
            self.weergeef_tarief = tarief


DESKTOP = r'C:\Users\dieter.KWEKERIJBAAS\Desktop'
TARIEFKAART = r'C:\Users\dieter.KWEKERIJBAAS\OneDrive - Kwekerij Baas\Claude Code\finance-automatisering\uzb-factuurcontrole\data\Tariefkaart_alle_UZB_01-01-2026.xlsx'


def laad_tariefkaart_tab(tab, colmap, pad=None):
    """Lees een bureau-tab uit de gecombineerde tariefkaart: schaal -> tarief-record.
    colmap = {rec_key: kolomindex} relatief t.o.v. de rij waar kolom 0 == 'Schaal'.
    Returns {schaalcode: {rec_key: tarief, ...}}."""
    pad = pad or TARIEFKAART
    if not os.path.exists(pad):
        return {}
    df = pd.read_excel(pad, sheet_name=tab, header=None)
    # vind header-rij met 'Schaal' in kolom 0
    hr = None
    for i in range(min(8, df.shape[0])):
        v = df.iloc[i, 0]
        if isinstance(v, str) and v.strip().lower() == 'schaal':
            hr = i
            break
    if hr is None:
        return {}
    out = {}
    for i in range(hr + 1, df.shape[0]):
        sch = df.iloc[i, 0]
        if not isinstance(sch, str) or not sch.strip():
            continue
        rec = {'schaal': sch.strip()}
        ok = False
        for key, ci in colmap.items():
            try:
                val = df.iloc[i, ci]
                rec[key] = float(val) if pd.notna(val) and str(val).replace('.', '').replace(',', '').strip() else None
                if rec[key] is not None:
                    ok = True
            except Exception:
                rec[key] = None
        if ok:
            out[sch.strip()] = rec
    return out


def vind_tariefbestand(map_pad, patroon, default=None):
    """Zoek de tarieventabel: eerst in de weekmap, dan op het bureaublad, dan default."""
    for d in (map_pad, DESKTOP):
        if not d:
            continue
        k = glob.glob(os.path.join(d, patroon))
        if k:
            return k[0]
    return default if (default and os.path.exists(default)) else None


def laad_schaal_generiek(pad, sheet, tarief_cols):
    """Lees een per-medewerker schaal/tarief-tab. tarief_cols = {rec_key: kolomindex}.
    Returns (op_tag, op_naam)."""
    if not pad or not os.path.exists(pad):
        return {}, {}
    df = pd.read_excel(pad, sheet_name=sheet, header=0)
    op_tag, op_naam = {}, {}
    for _, row in df.iterrows():
        if 'Nummer' not in df.columns or pd.isna(row['Nummer']):
            continue
        tag = str(int(row['Nummer']))
        rec = {'tag': tag, 'schaal': str(row['Schaal']).strip() if not pd.isna(row['Schaal']) else ''}
        for key, ci in tarief_cols.items():
            try:
                rec[key] = row.iloc[ci]
            except Exception:
                rec[key] = None
        op_tag[tag] = rec
        op_naam[normalize_naam(f"{row['Voornaam']} {row['Achternaam']}")] = rec
    return op_tag, op_naam


class BureauProfiel:
    code = 'XXX'
    naam = 'Onbekend bureau'
    techniek_tags = set()
    doorgegeven_hint = ''   # substring in bestandsnaam van doorgegeven-uren (bv. 'L1', 'SW')
    tariefkaart_tab = None  # tabnaam in de gecombineerde tariefkaart
    tariefkaart_cols = {}   # {rec_key: kolomindex} t.o.v. de 'Schaal'-rij

    def herkent(self, bestandsnaam, tekst):
        raise NotImplementedError

    def parse_factuur(self, text, factuurnr):
        """Return list[FactuurRegel]."""
        raise NotImplementedError

    def laad_schaal(self, map_pad):
        """Return (op_tag: dict, op_naam: dict). Leeg toegestaan als geen tabel."""
        return {}, {}

    def tariefkaart(self, pad=None):
        """Return {schaalcode: tarief-record} uit de gecombineerde tariefkaart."""
        if not self.tariefkaart_tab:
            return {}
        return laad_tariefkaart_tab(self.tariefkaart_tab, self.tariefkaart_cols, pad)

    def tarief_checks(self, regel, schaal):
        """Return list van (label, uren, factuur_tarief, schaal_tarief).
        Mapt de factuurcategorieën op de juiste schaalkolom van dit bureau."""
        return []


# =====================================================================
# PROFIEL 1 — Level One Uitzendbureau B.V. (werkend, getest op WK13)
# =====================================================================
class LevelOneUitzendbureau(BureauProfiel):
    code = 'L1'
    naam = 'Level One Uitzendbureau B.V.'
    techniek_tags = {'134', '574'}  # Patryk Kolodziej, Kamil Sliwa (GTB F4 Vast)
    doorgegeven_hint = 'L1'
    tarieven_default = r'C:\Users\dieter.KWEKERIJBAAS\Downloads\L1 Factuurcalculatie per 01-01-2026.xlsx'
    tariefkaart_tab = 'L1'
    tariefkaart_cols = {'t_100_135': 1, 't_150': 2, 't_200': 3, 't_bijzonder': 4}

    def herkent(self, bestandsnaam, tekst):
        b = bestandsnaam.lower()
        return 'level one uitzendbureau' in b

    def parse_factuur(self, text, factuurnr):
        regels = {}
        cur_week = cur_jaar = None
        cur = None
        for ln in text.split('\n'):
            ln = ln.strip()
            mw = re.match(r'^Week:\s*(\d{4})-(\d{2})', ln)
            if mw:
                cur_jaar, cur_week = mw.group(1), mw.group(2)
                continue
            mn = re.match(r'^Naam:\s*(.+)$', ln)
            if mn and cur_week:
                raw = mn.group(1).strip()
                mm = re.match(r'^((?:[A-Z]\.)+)\s+(.+?)\s*\(([^)]+)\)\s*$', raw)
                norm = normalize_naam(f'{mm.group(3)} {mm.group(2)}') if mm else normalize_naam(raw)
                key = (cur_week, norm)
                if key not in regels:
                    regels[key] = FactuurRegel(cur_week, cur_jaar, raw, norm, factuurnr)
                cur = regels[key]
                continue
            if cur is None:
                continue
            for pat, code in [
                (r'^Loon normale uren\s+(-?\d+:\d{2})\s+([\d,]+)\s+(-?[\d\.,]+)$', 'normaal'),
                (r'^Loon overwerkuren 135,00%\s+(-?\d+:\d{2})\s+([\d,]+)\s+(-?[\d\.,]+)$', 'ow135'),
                (r'^Loon overwerkuren 150,00%\s+(-?\d+:\d{2})\s+([\d,]+)\s+(-?[\d\.,]+)$', 'ow150'),
                (r'^Loon bijzondere uren 150,00%\s+(-?\d+:\d{2})\s+([\d,]+)\s+(-?[\d\.,]+)$', 'bijzonder150'),
            ]:
                m = re.match(pat, ln)
                if m:
                    cur.voeg_toe(code, hhmm_to_uren(m.group(1)), euro_to_float(m.group(2)), euro_to_float(m.group(3)))
                    break
        return list(regels.values())

    def laad_schaal(self, map_pad):
        pad = vind_tariefbestand(map_pad, 'L1 Factuurcalculatie*.xlsx', self.tarieven_default)
        # L1: Werknemers+schaal+tarief — 100%/135%(5), 150%(6), 200%(7), 150%bijzonder(8)
        return laad_schaal_generiek(pad, 'Werknemers+schaal+tarief',
                                    {'t_100_135': 5, 't_150': 6, 't_200': 7, 't_bijzonder': 8})

    def tarief_checks(self, regel, schaal):
        c = regel.categorieen
        uren_100_135 = c.get('normaal', {}).get('uren', 0) + c.get('ow135', {}).get('uren', 0)
        tarief_100_135 = c.get('normaal', {}).get('tarief') or c.get('ow135', {}).get('tarief')
        checks = []
        if uren_100_135:
            checks.append(('normale uren + OT135%', uren_100_135, tarief_100_135, schaal['t_100_135']))
        if c.get('ow150', {}).get('uren'):
            checks.append(('OT150%', c['ow150']['uren'], c['ow150']['tarief'], schaal['t_150']))
        if c.get('bijzonder150', {}).get('uren'):
            checks.append(('bijzondere uren 150%', c['bijzonder150']['uren'], c['bijzonder150']['tarief'], schaal['t_bijzonder']))
        return checks


# =====================================================================
# PROFIEL 2 — Level One Payroll bv (Jeugd + Volwassen)
# Zelfde factuursjabloon als Level One Uitzendbureau, andere tarieventabel.
# =====================================================================
class LevelOnePayroll(LevelOneUitzendbureau):
    code = 'LP'
    naam = 'Level One Payroll bv'
    techniek_tags = set()
    doorgegeven_hint = ['Jeugd', 'PL', 'Volwassen']   # Payroll = Jeugd + Volwassen; meerdere bestanden samenvoegen
    tarieven_default = r'C:\Users\dieter.KWEKERIJBAAS\Downloads\L1 jeugd en payroll Factuurcalculatie per 01-01-2026.xlsx'
    tariefkaart_tab = 'L1 jeugd-payroll'
    tariefkaart_cols = {'t_100_135': 1, 't_150': 2, 't_200': 3, 't_bijzonder': 4}

    def herkent(self, bestandsnaam, tekst):
        return 'level one payroll' in bestandsnaam.lower()

    # parse_factuur geërfd van Level One Uitzendbureau (zelfde sjabloon, HH:MM)

    def laad_schaal(self, map_pad):
        pad = vind_tariefbestand(map_pad, 'L1 jeugd en payroll Factuurcalculatie*.xlsx', self.tarieven_default)
        # Payroll: kolommen 100%(5), 150%(6), 200%(7). Geen apart 135/bijzonder.
        op_tag, op_naam = laad_schaal_generiek(pad, 'Werknemers+schaal+tarief',
                                               {'t_100_135': 5, 't_150': 6, 't_200': 7})
        for rec in op_tag.values():
            rec['t_bijzonder'] = rec.get('t_150')  # bijzonder150 valideren tegen 150%-kolom
        return op_tag, op_naam
    # tarief_checks geërfd: normaal+OT135 -> t_100_135 ; OT150 -> t_150 ; bijzonder150 -> t_bijzonder(=150%)


# =====================================================================
# PROFIEL 3 — Sterk werk Uitzendburo B.V.
# Format: "14 A.M. Antohi 38,00 100,00 uren 28,68 21,00 1.089,84"
#         vervolgregels "9,25 135,00 overuren 28,68 ..." en "30,00 50% 10,86 ..."
# LET OP: tarieventabel Sterk Werk nog NIET beschikbaar -> alleen uren-controle
#         (tarief_checks leeg) totdat de tabel is aangeleverd.
# =====================================================================
class SterkWerk(BureauProfiel):
    code = 'SW'
    naam = 'Sterk werk Uitzendburo B.V.'
    techniek_tags = set()
    doorgegeven_hint = 'SW'
    tariefkaart_tab = 'Sterk Werk'
    tariefkaart_cols = {'t_100_135': 1, 't_150': 2, 't_200': 3, 't_feestdag': 4, 't_nacht50': 5}

    def herkent(self, bestandsnaam, tekst):
        return 'sterk werk' in bestandsnaam.lower() or 'sterk werk' in tekst.lower()

    def parse_factuur(self, text, factuurnr):
        regels = {}
        cur = None
        # '-?' staat trailing-minus toe (creditnota: '38,00-', '1.143,42-')
        re_naam = re.compile(r'^(\d{1,2})\s+(.+?)\s+([\d.,]+-?)\s+([\d,]+)\s+(uren|overuren)\s+([\d,]+)\s+([\d,]+)\s+([\d.,]+-?)$')
        re_verv = re.compile(r'^([\d.,]+-?)\s+([\d,]+)\s+(uren|overuren)\s+([\d,]+)\s+([\d,]+)\s+([\d.,]+-?)$')
        re_50 = re.compile(r'^([\d.,]+-?)\s+50%?\s+([\d,]+)\s+([\d,]+)\s+([\d.,]+-?)$')

        def cat_van(perc, soort):
            p = int(round(abs(komma_to_float(perc))))
            if soort == 'uren' or p == 100:
                return 'normaal'
            if p == 135:
                return 'ow135'
            if p == 150:
                return 'ow150'
            return 'overig'

        for ln in text.split('\n'):
            ln = ln.strip()
            # Creditnota: zet een minus tussen twee getallen ('38,00-100,00') om naar '38,00- 100,00'
            ln = re.sub(r'(?<=\d)-(?=\d)', '- ', ln)
            m = re_naam.match(ln)
            if m:
                week, naam, aantal, perc, soort, tarief, btw, bedrag = m.groups()
                norm = normalize_naam(naam)
                key = (week, norm)
                if key not in regels:
                    regels[key] = FactuurRegel(week, '2026', naam.strip(), norm, factuurnr)
                cur = regels[key]
                cur.voeg_toe(cat_van(perc, soort), komma_to_float(aantal), komma_to_float(tarief), komma_to_float(bedrag))
                continue
            if cur is None:
                continue
            m = re_verv.match(ln)
            if m:
                aantal, perc, soort, tarief, btw, bedrag = m.groups()
                cur.voeg_toe(cat_van(perc, soort), komma_to_float(aantal), komma_to_float(tarief), komma_to_float(bedrag))
                continue
            m = re_50.match(ln)
            if m:
                aantal, tarief, btw, bedrag = m.groups()
                # 50%-nacht = toeslag op reeds getelde uren -> NIET als extra uren tellen
                cur.voeg_toe('nacht50', komma_to_float(aantal), komma_to_float(tarief), komma_to_float(bedrag), tel_uren=False)
                continue
        return list(regels.values())

    tarieven_default = r'C:\Users\dieter.KWEKERIJBAAS\Desktop\Sterk werk  Factuurcalculatie per 01-01-2026.xlsx'

    def laad_schaal(self, map_pad):
        pad = vind_tariefbestand(map_pad, 'Sterk werk*Factuurcalculatie*.xlsx', self.tarieven_default)
        # Medewerkers+schaal+tarief: 100%/135%(5), 150%(6), 200%(7), Feestdag(8), 50% nacht(9)
        return laad_schaal_generiek(pad, 'Medewerkers+schaal+tarief',
                                    {'t_100_135': 5, 't_150': 6, 't_200': 7, 't_feestdag': 8, 't_nacht50': 9})

    def tarief_checks(self, regel, schaal):
        c = regel.categorieen
        uren_100_135 = c.get('normaal', {}).get('uren', 0) + c.get('ow135', {}).get('uren', 0)
        tarief_100_135 = c.get('normaal', {}).get('tarief') or c.get('ow135', {}).get('tarief')
        checks = []
        if uren_100_135:
            checks.append(('normale uren + OT135%', uren_100_135, tarief_100_135, schaal.get('t_100_135')))
        if c.get('ow150', {}).get('uren'):
            checks.append(('OT150%', c['ow150']['uren'], c['ow150']['tarief'], schaal.get('t_150')))
        if c.get('nacht50', {}).get('uren'):
            checks.append(('50% nachtuur', c['nacht50']['uren'], c['nacht50']['tarief'], schaal.get('t_nacht50')))
        return checks


# =====================================================================
# PROFIEL 4 — CervoKordaat (Kordaat Agri B.V.)
# Format: "De heer M.L. Ailincai" / "WK: 202514 Normale uren 27,75 100,00 € 27,00 21,00 € 749,25"
#         "Overuren 1,75 135,00 € 27,00 ..."
# LET OP: tarieventabel CervoKordaat nog NIET beschikbaar -> alleen uren-controle.
# =====================================================================
class CervoKordaat(BureauProfiel):
    code = 'CK'
    naam = 'CervoKordaat / Workstead (Kordaat Agri B.V.)'
    techniek_tags = set()
    doorgegeven_hint = 'KT'   # doorgegeven-uren heten "WK XX KT" (KordaaT)
    tariefkaart_tab = 'Cervokordaat'
    tariefkaart_cols = {'t_100': 1, 't_135': 2, 't_150': 3, 't_150_2': 6, 't_200': 7}

    def herkent(self, bestandsnaam, tekst):
        s = (bestandsnaam + ' ' + tekst).lower()
        return 'cervokordaat' in s or 'kordaat' in s or 'workstead' in s

    @staticmethod
    def _nums(s):
        return re.findall(r'\d[\d.]*,\d+|\d+', s)

    def parse_factuur(self, text, factuurnr):
        regels = {}
        cur_naam = cur_norm = None
        for ln in text.split('\n'):
            ln = ln.strip()
            mn = re.match(r'^(?:De heer|Mevrouw|Mevr\.?|Dhr\.?)\s+(.+)$', ln)
            if mn:
                cur_naam = mn.group(1).strip()
                cur_norm = normalize_naam(cur_naam)
                continue
            # WK-regel met normale uren
            mw = re.match(r'^WK:\s*(\d{4})(\d{2})\s+Normale uren\s+(.+)$', ln)
            if mw and cur_norm:
                jaar, week, rest = mw.group(1), mw.group(2), mw.group(3)
                nums = self._nums(rest)  # aantal, perc, tarief, btw, bedrag
                if len(nums) >= 5:
                    aantal, perc, tarief, btw, bedrag = nums[0], nums[1], nums[2], nums[3], nums[4]
                    key = (week, cur_norm)
                    if key not in regels:
                        regels[key] = FactuurRegel(week, jaar, cur_naam, cur_norm, factuurnr)
                    regels[key].voeg_toe('normaal', komma_to_float(aantal), komma_to_float(tarief), komma_to_float(bedrag))
                    self._laatste = key
                continue
            mo = re.match(r'^Overuren\s+(.+)$', ln)
            if mo and cur_norm and getattr(self, '_laatste', None):
                nums = self._nums(mo.group(1))
                if len(nums) >= 5:
                    aantal, perc, tarief, btw, bedrag = nums[0], nums[1], nums[2], nums[3], nums[4]
                    p = int(round(komma_to_float(perc)))
                    code = 'ow150' if p == 150 else 'ow135'
                    regels[self._laatste].voeg_toe(code, komma_to_float(aantal), komma_to_float(tarief), komma_to_float(bedrag))
                continue
        return list(regels.values())

    tarieven_default = r'C:\Users\dieter.KWEKERIJBAAS\Desktop\Cervokordaat Factuurcalculatie 01-01-2026.xlsx'

    def laad_schaal(self, map_pad):
        pad = vind_tariefbestand(map_pad, 'Cervokordaat*Factuurcalculatie*.xlsx', self.tarieven_default)
        # Werknemers+tarief+schaal: 100%(5), 135%(6), 150%(7), 150%2(8), 200%(9) — APART 100/135
        return laad_schaal_generiek(pad, 'Werknemers+tarief+schaal',
                                    {'t_100': 5, 't_135': 6, 't_150': 7, 't_150_2': 8, 't_200': 9})

    def tarief_checks(self, regel, schaal):
        c = regel.categorieen
        checks = []
        if c.get('normaal', {}).get('uren'):
            checks.append(('normale uren', c['normaal']['uren'], c['normaal']['tarief'], schaal.get('t_100')))
        if c.get('ow135', {}).get('uren'):
            checks.append(('OT135%', c['ow135']['uren'], c['ow135']['tarief'], schaal.get('t_135')))
        if c.get('ow150', {}).get('uren'):
            checks.append(('OT150%', c['ow150']['uren'], c['ow150']['tarief'], schaal.get('t_150')))
        return checks


# =====================================================================
# REGISTER van actieve bureaus.
# =====================================================================
BUREAUS = [
    LevelOneUitzendbureau(),
    LevelOnePayroll(),
    SterkWerk(),
    CervoKordaat(),
]


def herken_bureau(bestandsnaam, tekst):
    """Geef het eerste bureau-profiel dat deze factuur herkent, of None."""
    for prof in BUREAUS:
        try:
            if prof.herkent(bestandsnaam, tekst):
                return prof
        except Exception:
            continue
    return None
