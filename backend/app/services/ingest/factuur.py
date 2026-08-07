"""UZB-facturen (.pdf) inlezen (SPEC §7).

Elk uitzendbureau heeft een eigen factuuropmaak:

**Level One** — per uitzendkracht een blok met een kopregel en daaronder de
regels per loonbestanddeel::

    Week: 2026-25
    Naam: K.P. Sliwa (Kamil)
    Loon normale uren            38:00          33,372   1.268,06
    Loon overwerkuren  135,00%    7:00          33,372     233,59

**Sterk Werk** — één regel per bestanddeel, met de naam alleen op de eerste
regel van een uitzendkracht::

    25 A.I. Boca   38,00 100,00 uren      29,43 21,00 1.118,34
                   10,00 135,00 overuren  29,43 21,00   294,30

De naam staat op beide facturen als initialen plus achternaam; Level One zet de
roepnaam tussen haakjes. Het koppelen aan onze eigen namen gebeurt daarom apart
(zie `services/factuurcontrole.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path

import pdfplumber

# Level One
_L1_NAAM = re.compile(r"^Naam:\s*(.+?)\s*$")
_L1_WEEK = re.compile(r"^Week:\s*(\d{4})-(\d{1,2})")
_L1_REGEL = re.compile(
    r"^(?P<oms>Loon\s+\D+?)\s+(?:(?P<toeslag>[\d,]+)%\s+)?"
    r"(?P<uren>\d+:\d{2})\s+(?P<tarief>[\d.,]+)\s+(?P<bedrag>[\d.,]+)$"
)
_L1_OVERIG = re.compile(r"^(?P<oms>[A-Z][\w\s]+?)\s+(?P<aantal>[\d,]+)\s+(?P<tarief>[\d.,]+)\s+(?P<bedrag>[\d.,]+)$")
_L1_FACTUUR = re.compile(r"^\d+\s+(\d+)\s+\d\d-\d\d-\d{4}€\s*([\d.,]+)")

# Sterk Werk
_SW_EERSTE = re.compile(
    r"^(?P<week>\d{1,2})\s+(?P<naam>[A-Z](?:\.[A-Z])*\.?\s+\S+)\s+"
    r"(?P<aantal>[\d.,]+)\s+(?P<pct>[\d,]+)\s+(?P<soort>\w+)\s+"
    r"(?P<tarief>[\d.,]+)\s+[\d,]+\s+(?P<bedrag>[\d.,]+)$"
)
_SW_VERVOLG = re.compile(
    r"^(?P<aantal>[\d.,]+)\s+(?P<pct>[\d,]+)\s+(?P<soort>\w+)\s+"
    r"(?P<tarief>[\d.,]+)\s+[\d,]+\s+(?P<bedrag>[\d.,]+)$"
)
_SW_FACTUUR = re.compile(r"Factuurnummer\s*:\s*(\d+)")
_SW_TOTAAL = re.compile(r"^([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)$")


def _getal(tekst: str) -> Decimal | None:
    try:
        return Decimal(str(tekst).replace(".", "").replace(",", "."))
    except (InvalidOperation, AttributeError):
        return None


def _uren(tekst: str) -> Decimal:
    """"38:00" of "38,00" -> uren als decimaal."""
    if ":" in tekst:
        uur, minuut = tekst.split(":")
        return Decimal(uur) + Decimal(minuut) / Decimal(60)
    return _getal(tekst) or Decimal("0")


@dataclass
class FactuurRegel:
    omschrijving: str
    percentage: str | None
    uren: Decimal
    tarief: Decimal
    bedrag: Decimal


@dataclass
class FactuurKracht:
    """Alle factuurregels van één uitzendkracht."""

    naam_ruw: str
    regels: list[FactuurRegel] = field(default_factory=list)

    @property
    def uren(self) -> Decimal:
        return sum((r.uren for r in self.regels), Decimal("0"))

    @property
    def bedrag(self) -> Decimal:
        return sum((r.bedrag for r in self.regels), Decimal("0"))


@dataclass
class Factuur:
    uzb_sleutel: str
    factuurnummers: list[str] = field(default_factory=list)
    krachten: list[FactuurKracht] = field(default_factory=list)
    totaal_op_factuur: Decimal | None = None

    @property
    def uren(self) -> Decimal:
        return sum((k.uren for k in self.krachten), Decimal("0"))

    @property
    def bedrag(self) -> Decimal:
        return sum((k.bedrag for k in self.krachten), Decimal("0"))


def _regels_uit_pdf(bron) -> list[str]:
    data = BytesIO(bron) if isinstance(bron, (bytes, bytearray)) else bron
    tekst: list[str] = []
    with pdfplumber.open(data) as pdf:
        for pagina in pdf.pages:
            tekst.extend((pagina.extract_text() or "").split("\n"))
    return tekst


def _lees_level_one(regels: list[str]) -> Factuur:
    factuur = Factuur(uzb_sleutel="L1")
    per_naam: dict[str, FactuurKracht] = {}
    huidige: FactuurKracht | None = None

    for regel in regels:
        if (m := _L1_NAAM.match(regel)):
            naam = m.group(1)
            huidige = per_naam.get(naam)
            if huidige is None:
                huidige = FactuurKracht(naam_ruw=naam)
                per_naam[naam] = huidige
                factuur.krachten.append(huidige)
            continue
        if (m := _L1_FACTUUR.match(regel)):
            factuur.factuurnummers.append(m.group(1))
            factuur.totaal_op_factuur = _getal(m.group(2))
            continue
        if huidige is None:
            continue
        if (m := _L1_REGEL.match(regel)):
            huidige.regels.append(
                FactuurRegel(
                    omschrijving=m.group("oms").strip(),
                    percentage=m.group("toeslag"),
                    uren=_uren(m.group("uren")),
                    tarief=_getal(m.group("tarief")) or Decimal("0"),
                    bedrag=_getal(m.group("bedrag")) or Decimal("0"),
                )
            )
        elif (m := _L1_OVERIG.match(regel)) and "Totaal" not in regel:
            # bv. een reiskostenvergoeding: telt in het bedrag maar niet in uren
            huidige.regels.append(
                FactuurRegel(
                    omschrijving=m.group("oms").strip(),
                    percentage=None,
                    uren=Decimal("0"),
                    tarief=_getal(m.group("tarief")) or Decimal("0"),
                    bedrag=_getal(m.group("bedrag")) or Decimal("0"),
                )
            )
    return factuur


def _lees_sterk_werk(regels: list[str]) -> Factuur:
    factuur = Factuur(uzb_sleutel="SW")
    huidige: FactuurKracht | None = None

    for regel in regels:
        gestript = regel.strip()
        if (m := _SW_FACTUUR.search(gestript)):
            if m.group(1) not in factuur.factuurnummers:
                factuur.factuurnummers.append(m.group(1))
            continue
        if (m := _SW_EERSTE.match(gestript)):
            huidige = FactuurKracht(naam_ruw=re.sub(r"\s+", " ", m.group("naam")))
            factuur.krachten.append(huidige)
        elif (m := _SW_VERVOLG.match(gestript)) and huidige is not None:
            pass
        else:
            if gestript.startswith("Sub-totaal") or "Totaal aantal uren" in gestript:
                huidige = None
            continue
        huidige.regels.append(
            FactuurRegel(
                omschrijving=m.group("soort"),
                percentage=m.group("pct"),
                uren=_uren(m.group("aantal")),
                tarief=_getal(m.group("tarief")) or Decimal("0"),
                bedrag=_getal(m.group("bedrag")) or Decimal("0"),
            )
        )
    return factuur


def lees_factuur(bron: str | Path | bytes, uzb_sleutel: str | None = None) -> Factuur:
    """Lees een UZB-factuur. Het uitzendbureau wordt herkend aan de opmaak."""
    regels = _regels_uit_pdf(bron)
    tekst = "\n".join(regels)

    if "Naam:" in tekst and "Loon normale uren" in tekst:
        herkend = "L1"
    elif "Uitzendkracht" in tekst and "Factuurnummer" in tekst:
        herkend = "SW"
    else:
        herkend = None

    if uzb_sleutel is None:
        if herkend is None:
            raise ValueError(
                "factuuropmaak niet herkend; kies het uitzendbureau handmatig"
            )
        uzb_sleutel = herkend
    elif herkend is not None and not (
        {uzb_sleutel, herkend} <= {"L1", "L1_JEUGD"} or uzb_sleutel == herkend
    ):
        # Anders wordt de factuur van het ene bureau afgezet tegen de uren van
        # het andere.
        raise ValueError(
            f"deze factuur heeft de opmaak van {herkend}, maar er is "
            f"{uzb_sleutel} gekozen"
        )

    factuur = (
        _lees_level_one(regels) if uzb_sleutel in ("L1", "L1_JEUGD") else _lees_sterk_werk(regels)
    )
    factuur.uzb_sleutel = uzb_sleutel
    if not factuur.krachten:
        raise ValueError("geen factuurregels herkend in dit bestand")
    return factuur
