"""Nitea 'Medewerker uren'-overzicht (.pdf) inlezen: werkelijke registratie.

Regelindeling per gewerkte dag:
    <Nr> <NiteaID> - <Naam> <DD-MM-YYYY> <begin> <einde> <werktijd> <pauze>
bv. `1 87 - Marius Mic 15-06-2026 6:59 16:02 7:45 1:15`

'Werk tijd' is de netto gewerkte tijd (pauze er al af); 'Pauze tijd' apart.
Bij een korte dienst staat er geen pauze; die kolom is dan leeg en de regel
eindigt na de werktijd. Kop-/voetregels (titels, perioderegel, paginanummers)
matchen het patroon niet en worden overgeslagen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
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
    r"(?P<eind>\d{1,2}:\d{2})\s+"
    r"(?P<werk>\d{1,2}:\d{2})"
    # Zonder pauze eindigt de regel na de werktijd; die dienst telt gewoon mee.
    r"(?:\s+(?P<pauze>\d{1,2}:\d{2}))?\s*$"
)


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


def lees_nitea(bron: str | Path | bytes) -> list[NiteaMedewerker]:
    """Parse een Nitea-PDF naar één NiteaMedewerker per medewerker."""
    data = BytesIO(bron) if isinstance(bron, (bytes, bytearray)) else bron
    per_id: dict[str, NiteaMedewerker] = {}

    with pdfplumber.open(data) as pdf:
        for pagina in pdf.pages:
            tekst = pagina.extract_text() or ""
            for regel in tekst.split("\n"):
                m = _REGEL.match(regel)
                if not m:
                    continue
                nid = m.group("id")
                naam = re.sub(r"\s+", " ", m.group("naam")).strip()
                datum = datetime.strptime(m.group("datum"), "%d-%m-%Y").date()
                begin = _tijd(m.group("begin"))
                eind = _tijd(m.group("eind"))
                werk = _hm_naar_min(m.group("werk"))
                pauze = _hm_naar_min(m.group("pauze") or "0:00")

                mw = per_id.setdefault(nid, NiteaMedewerker(naam=naam, nitea_id=nid))
                mw.registratie.append(
                    RegistratieRegel(datum, begin, eind, werk, pauze)
                )

    return sorted(per_id.values(), key=lambda m: m.naam)
