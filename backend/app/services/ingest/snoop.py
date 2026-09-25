"""SNOOP-export (.xlsx) inlezen: geplande inzet + loonschaal per medewerker.

Kolommen (rij 1 = header, data vanaf rij 2):
    Registratienummer | Medewerker | Datum | Starttijd | Eindtijd |
    Werkelijke starttijd | Werkelijke eindtijd | Gewerkte uren | Locatie |
    Werkgever op datum shift | Type uitzendkracht | Tarief uitzendbureau
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

from app.services.calc.types import PlanningRegel

# header-namen -> interne sleutel (case-insensitief, spaties genegeerd)
_KOLOMMEN = {
    "medewerker": "medewerker",
    "datum": "datum",
    "starttijd": "start",
    "eindtijd": "eind",
    "gewerkteuren": "uren",
    "tariefuitzendbureau": "loonschaal",
    "werkgeveropdatumshift": "werkgever",
}

_VERPLICHT = {"medewerker", "datum", "start", "eind"}
# Hoeveel rijen er naar de kopregel wordt gezocht voordat het bestand als
# onbruikbaar geldt.
_MAX_KOPREGEL = 15


def _norm_naam(naam: str) -> str:
    return re.sub(r"\s+", " ", str(naam)).strip()


def _als_tijd(waarde) -> time | None:
    if waarde is None or waarde == "":
        return None
    if isinstance(waarde, time):
        return waarde
    if isinstance(waarde, datetime):
        return waarde.time()
    m = re.match(r"^(\d{1,2}):(\d{2})", str(waarde).strip())
    if not m:
        return None
    uur, minuut = int(m.group(1)), int(m.group(2))
    # "24:00" is middernacht aan het eind van de dag; zonder deze grens sloeg
    # het hele bestand af op "hour must be in 0..23".
    if uur == 24 and minuut == 0:
        return time(0, 0)
    if not (0 <= uur <= 23 and 0 <= minuut <= 59):
        return None
    return time(uur, minuut)


def _als_datum(waarde) -> date | None:
    if isinstance(waarde, datetime):
        return waarde.date()
    if isinstance(waarde, date):
        return waarde
    if waarde is None:
        return None
    s = str(waarde).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y", "%d-%m-%Y %H:%M:%S",
        "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y", "%d-%m-%y", "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _als_minuten(uren_waarde) -> int | None:
    """'Gewerkte uren' (bv. 8.25) -> minuten (netto, pauze er al af).

    De cel is meestal een getal, maar kan ook tekst met een decimale komma
    ("8,00") of een tijdnotatie ("7:45") zijn, en Excel levert een duur soms
    als tijd of timedelta. Elk van die vormen gaf eerder `None`, waarna er werd
    teruggevallen op eind - begin: de pauze telde dan mee als gewerkte tijd.
    """
    if uren_waarde is None or uren_waarde == "":
        return None
    if isinstance(uren_waarde, timedelta):
        return int(uren_waarde.total_seconds() // 60)
    if isinstance(uren_waarde, datetime):
        uren_waarde = uren_waarde.time()
    if isinstance(uren_waarde, time):
        return uren_waarde.hour * 60 + uren_waarde.minute
    tekst = str(uren_waarde).strip()
    if (m := re.match(r"^(\d{1,2}):(\d{2})$", tekst)):
        return int(m.group(1)) * 60 + int(m.group(2))
    try:
        return int((Decimal(tekst.replace(",", ".")) * 60).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


@dataclass
class SnoopMedewerker:
    naam: str
    loonschaal: str | None
    planning: list[PlanningRegel] = field(default_factory=list)
    # Naam van het uitzendbureau zoals SNOOP hem noteert; wordt gebruikt om te
    # controleren dat het bestand bij het gekozen bureau hoort.
    werkgever: str | None = None


def lees_snoop(
    bron: str | Path | bytes, overgeslagen: list[str] | None = None
) -> list[SnoopMedewerker]:
    """Parse een SNOOP-export naar één SnoopMedewerker per medewerker.

    `overgeslagen` (optioneel) wordt gevuld met rijen die een medewerker noemen
    maar geen bruikbare datum of tijd hebben. Zulke rijen verdwenen eerder
    zonder spoor, waardoor iemand met alleen zulke rijen nergens meer voorkwam
    -- geen loonschaal, niet meegeteld, geen waarschuwing.
    """
    data = BytesIO(bron) if isinstance(bron, (bytes, bytearray)) else bron
    wb = load_workbook(data, data_only=True)
    # De kopregel staat niet altijd op de eerste rij: exports beginnen soms met
    # een titel of lege regels. Daarom wordt er per tabblad naar gezocht.
    ws = None
    idx: dict[str, int] = {}
    rijen = iter(())
    gezien: list[str] = []
    for blad in wb.worksheets:
        alle = list(blad.iter_rows(max_row=_MAX_KOPREGEL, values_only=True))
        for nummer, header in enumerate(alle, start=1):
            kandidaat = {
                _KOLOMMEN[sleutel]: i
                for i, cel in enumerate(header)
                if (sleutel := re.sub(r"\s+", "", str(cel or "").lower())) in _KOLOMMEN
            }
            if _VERPLICHT <= kandidaat.keys():
                ws, idx = blad, kandidaat
                rijen = blad.iter_rows(min_row=nummer + 1, values_only=True)
                break
            gezien += [str(c).strip() for c in header if c and str(c).strip()]
        if ws is not None:
            break

    if ws is None:
        gevonden = ", ".join(dict.fromkeys(gezien)) or "geen"
        raise ValueError(
            "de kolomkoppen zijn niet herkend. Verwacht worden 'Medewerker', "
            "'Datum', 'Starttijd' en 'Eindtijd'. Gevonden koppen: " + gevonden
        )

    per_naam: dict[str, SnoopMedewerker] = {}
    schaal_stemmen: dict[str, Counter] = {}

    for rij in rijen:
        naam_ruw = rij[idx["medewerker"]] if idx["medewerker"] < len(rij) else None
        if not naam_ruw:
            continue
        naam = _norm_naam(naam_ruw)
        datum = _als_datum(rij[idx["datum"]])
        begin = _als_tijd(rij[idx["start"]])
        eind = _als_tijd(rij[idx["eind"]])
        if datum is None or begin is None or eind is None:
            if overgeslagen is not None:
                ontbreekt = ", ".join(
                    naam
                    for naam, waarde in (
                        ("datum", datum), ("starttijd", begin), ("eindtijd", eind)
                    )
                    if waarde is None
                )
                overgeslagen.append(
                    f"{naam}: rij zonder bruikbare {ontbreekt} "
                    f"({rij[idx['datum']]!r}, {rij[idx['start']]!r}, "
                    f"{rij[idx['eind']]!r})"
                )
            continue

        minuten = _als_minuten(rij[idx["uren"]]) if "uren" in idx else None
        if minuten is None:  # val terug op begin/eind
            bruto = (eind.hour * 60 + eind.minute) - (begin.hour * 60 + begin.minute)
            if bruto <= 0:
                bruto += 24 * 60
            minuten = bruto

        # Op kleine letters groeperen: "MARIUS MIC" en "Marius Mic" zijn
        # dezelfde persoon. Twee aparte regels leverden verderop één winnaar op
        # en daarmee verdween de loonschaal van de ander.
        sleutel = naam.lower()
        mw = per_naam.setdefault(sleutel, SnoopMedewerker(naam=naam, loonschaal=None))
        mw.planning.append(PlanningRegel(datum, begin, eind, minuten))

        if "loonschaal" in idx and idx["loonschaal"] < len(rij):
            schaal = rij[idx["loonschaal"]]
            if schaal:
                schaal_stemmen.setdefault(sleutel, Counter())[str(schaal).strip()] += 1

        if "werkgever" in idx and idx["werkgever"] < len(rij):
            werkgever = rij[idx["werkgever"]]
            if werkgever and not mw.werkgever:
                mw.werkgever = str(werkgever).strip()

    for sleutel, teller in schaal_stemmen.items():
        per_naam[sleutel].loonschaal = teller.most_common(1)[0][0]

    return sorted(per_naam.values(), key=lambda m: m.naam)
