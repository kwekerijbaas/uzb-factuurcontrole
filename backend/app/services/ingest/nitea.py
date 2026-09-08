"""Nitea 'Medewerker uren'-overzicht (.pdf) inlezen: werkelijke registratie.

Regelindeling per gewerkte dag:
    <Nr> <NiteaID> - <Naam> <DD-MM-YYYY> <begin> <einde> <werktijd> <pauze>
bv. `1 87 - Marius Mic 15-06-2026 6:59 16:02 7:45 1:15`

'Werk tijd' is de netto gewerkte tijd (pauze er al af); 'Pauze tijd' apart.
Bij een korte dienst staat er geen pauze; die kolom is dan leeg en de regel
eindigt na de werktijd. Kop-/voetregels (titels, perioderegel, paginanummers)
matchen het patroon niet en worden overgeslagen.

Nachtdiensten: een dienst over middernacht kan met een einddatum vóór de
eindtijd staan (`03-08-2026 22:57 04-08-2026 8:00 8:00 1:00`); die wordt
gelezen en de engine splitst hem op middernacht. Staat de eindtijd er niet
(regel met drie tijden waarvan begin-einde de werktijd bij lange na niet
verklaart), dan wordt de eindtijd uit begin + werktijd + pauze afgeleid in
plaats van een dienst van zestien uur met één gewerkt uur aan te nemen.

Regels die op een registratieregel lijken maar niet te lezen zijn, worden
in `overgeslagen` verzameld zodat de gebruiker ze te zien krijgt: een stil
weggelaten dag is een te laag weektotaal dat niemand opmerkt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from io import BytesIO
from pathlib import Path

import pdfplumber

from app.services.calc.types import RegistratieRegel

_REGEL = re.compile(
    r"^\s*\d+\s+"                       # volgnummer
    r"(?P<id>\d+)\s*-\s*"              # Nitea-ID
    r"(?P<naam>.+?)\s+"               # naam (non-greedy)
    r"(?P<datum>\d{2}-\d{2}-\d{4})\s+"
    r"(?P<begin>\d{1,2}:\d{2})\s+"
    # Nachtdienst: soms staat de einddatum vóór de eindtijd.
    r"(?:(?P<einddatum>\d{2}-\d{2}-\d{4})\s+)?"
    r"(?P<eind>\d{1,2}:\d{2})\s+"
    r"(?P<werk>\d{1,2}:\d{2})"
    # Zonder pauze eindigt de regel na de werktijd; die dienst telt gewoon mee.
    r"(?:\s+(?P<pauze>\d{1,2}:\d{2}))?\s*$"
)

# Lijkt op een registratieregel (volgnummer, Nitea-ID, streepje, een datum)
# maar is niet volgens `_REGEL` te lezen. Zulke regels worden gemeld.
_LIJKT_OP_REGEL = re.compile(r"^\s*\d+\s+\d+\s*-\s*\S.*\d{2}-\d{2}-\d{4}")

# Boven dit verschil tussen (einde - begin) en de werktijd is een regel met
# drie tijden eerder 'eindtijd ontbreekt' dan 'dienst met een lange
# onderbreking'. Vier uur: een echte split shift blijft daaronder.
_ONVERKLAARBAAR = 4 * 60


def _hm_naar_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _tijd(s: str) -> time:
    h, m = s.split(":")
    return time(int(h) % 24, int(m))


@dataclass
class NiteaMedewerker:
    naam: str
    nitea_id: str
    registratie: list[RegistratieRegel] = field(default_factory=list)


def _regel_uit(m: re.Match) -> tuple[RegistratieRegel, str | None]:
    """Maak van een gelezen regel een registratieregel.

    Geeft ook een opmerking terug als de regel anders gelezen is dan hij er
    staat (afgeleide eindtijd), zodat dat in het overzicht terechtkomt.
    """
    datum = datetime.strptime(m.group("datum"), "%d-%m-%Y").date()
    begin = _tijd(m.group("begin"))
    eind = _tijd(m.group("eind"))
    werk = _hm_naar_min(m.group("werk"))
    pauze = _hm_naar_min(m.group("pauze") or "0:00")
    opmerking = None

    if m.group("einddatum"):
        einddatum = datetime.strptime(m.group("einddatum"), "%d-%m-%Y").date()
        if einddatum != datum and einddatum != datum + timedelta(days=1):
            opmerking = (
                f"{datum:%d-%m}: einddatum {einddatum:%d-%m-%Y} ligt niet op de "
                "dag zelf of de dag erna; als dienst over middernacht gelezen"
            )

    elif m.group("pauze") is None:
        # Drie tijden: (begin, einde, werk) zonder pauze, óf (begin, werk,
        # pauze) zonder eindtijd. Verklaart begin-einde de werktijd bij lange
        # na niet, dan is het de tweede lezing.
        venster = (_naar_min(eind) - _naar_min(begin)) % (24 * 60) or 24 * 60
        if venster - werk > _ONVERKLAARBAAR:
            werk_alt, pauze_alt = _naar_min(eind), werk
            if 0 < werk_alt <= 16 * 60:
                eind_alt = (_naar_min(begin) + werk_alt + pauze_alt) % (24 * 60)
                opmerking = (
                    f"{datum:%d-%m}: geen eindtijd in Nitea; gelezen als "
                    f"{begin:%H:%M} + {werk_alt // 60}:{werk_alt % 60:02d} werk "
                    f"+ {pauze_alt // 60}:{pauze_alt % 60:02d} pauze = einde "
                    f"{eind_alt // 60:02d}:{eind_alt % 60:02d}"
                )
                eind, werk, pauze = time(eind_alt // 60, eind_alt % 60), werk_alt, pauze_alt

    return RegistratieRegel(datum, begin, eind, werk, pauze), opmerking


def _naar_min(t: time) -> int:
    return t.hour * 60 + t.minute


def lees_nitea(
    bron: str | Path | bytes, overgeslagen: list[str] | None = None
) -> list[NiteaMedewerker]:
    """Parse een Nitea-PDF naar één NiteaMedewerker per medewerker.

    `overgeslagen` (optioneel) wordt gevuld met regels die op een
    registratieregel lijken maar niet te lezen waren, en met opmerkingen over
    regels die anders gelezen zijn dan ze er staan.
    """
    data = BytesIO(bron) if isinstance(bron, (bytes, bytearray)) else bron
    per_id: dict[str, NiteaMedewerker] = {}

    with pdfplumber.open(data) as pdf:
        for pagina in pdf.pages:
            tekst = pagina.extract_text() or ""
            for regel in tekst.split("\n"):
                m = _REGEL.match(regel)
                if not m:
                    if overgeslagen is not None and _LIJKT_OP_REGEL.match(regel):
                        overgeslagen.append(re.sub(r"\s+", " ", regel).strip())
                    continue
                nid = m.group("id")
                naam = re.sub(r"\s+", " ", m.group("naam")).strip()
                registratie, opmerking = _regel_uit(m)
                if opmerking and overgeslagen is not None:
                    overgeslagen.append(f"{naam} {opmerking}")

                mw = per_id.setdefault(nid, NiteaMedewerker(naam=naam, nitea_id=nid))
                mw.registratie.append(registratie)

    return sorted(per_id.values(), key=lambda m: m.naam)
