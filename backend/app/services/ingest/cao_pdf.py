"""CAO-loontabel uit de gepubliceerde PDF inlezen (SPEC §6).

De cao-partijen publiceren de loontabel als PDF. De tabel heeft één kolom per
schaalletter (B t/m H) en één rij per leeftijd (jeugd) of trede (volwassenen):

    Trede/schaal      B       C       D       E       F       G       H
    15 jaar        6,00    6,16    6,32    6,43    6,70    7,02    7,56
    ...
    2             14,99   15,41   15,79   16,08   16,74   17,54   18,90
    ...
    8                                     19,65   20,73   21,94   23,91
    11                                                            26,85

De onderste treden bestaan alleen bij de hogere schalen en staan dus rechts
uitgelijnd. Bij het uitlezen van de tekst vallen die bedragen op volgorde
verkeerd; daarom worden ze gekoppeld op **x-positie** -- elk bedrag hoort bij de
kolomletter waar het onder staat.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path

import pdfplumber

from app.services.tarief.kaart import Loontabel

_LETTERS = "BCDEFGH"
_BEDRAG = re.compile(r"^\d{1,3},\d{2}$")
_JEUGD = re.compile(r"^(1[0-9])\s*jaar", re.IGNORECASE)
_TREDE = re.compile(r"^(\d{1,2})$")
_REGELHOOGTE = 6  # punten; label en bedragen staan zelden exact even hoog

_MAANDEN = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "augustus": 8, "september": 9, "oktober": 10, "november": 11,
    "december": 12,
}
_DATUM = re.compile(
    r"vanaf\s+(\d{1,2})\s+(" + "|".join(_MAANDEN) + r")\s+(\d{4})", re.IGNORECASE
)


def _bedrag(tekst: str) -> Decimal | None:
    try:
        waarde = Decimal(tekst.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None
    return waarde if waarde > 0 else None


def lees_cao_pdf(
    bron: str | Path | bytes, naam: str | None = None, ingangsdatum: date | None = None
) -> tuple[Loontabel, list[str]]:
    """Lees de CAO-loontabel uit de PDF.

    `ingangsdatum` wordt uit de kop gehaald ("geldend vanaf 1 augustus 2026")
    tenzij hij wordt meegegeven -- dan telt de opgegeven datum.
    """
    data = BytesIO(bron) if isinstance(bron, (bytes, bytearray)) else bron
    lonen: dict[str, Decimal] = {}
    waarschuwingen: list[str] = []
    gevonden_datum: date | None = None
    kolommen: dict[str, float] = {}

    with pdfplumber.open(data) as pdf:
        for pagina in pdf.pages:
            if gevonden_datum is None:
                m = _DATUM.search(pagina.extract_text() or "")
                if m:
                    gevonden_datum = date(
                        int(m.group(3)), _MAANDEN[m.group(2).lower()], int(m.group(1))
                    )

            regels: dict[int, list[dict]] = defaultdict(list)
            for woord in pagina.extract_words():
                regels[round(woord["top"])].append(woord)

            # kolompositie per schaalletter uit de koprij
            for top in sorted(regels):
                letters = {
                    w["text"].upper(): w["x0"]
                    for w in regels[top]
                    if w["text"].upper() in _LETTERS
                }
                if len(letters) >= len(_LETTERS) - 1:
                    kolommen = letters
                    break
            if not kolommen:
                continue

            # Splits elke regel in een label (de woorden vóór het eerste bedrag)
            # en de bedragen zelf. In de publicatie staan die soms op dezelfde
            # regel en soms op twee opeenvolgende regels.
            gesorteerd = sorted(regels)
            ontleed: dict[int, tuple[str, list[dict]]] = {}
            for top in gesorteerd:
                woorden = sorted(regels[top], key=lambda w: w["x0"])
                bedragen = [w for w in woorden if _BEDRAG.match(w["text"])]
                label_woorden = []
                for w in woorden:
                    if _BEDRAG.match(w["text"]) or w["text"] == "€":
                        break
                    label_woorden.append(w["text"])
                ontleed[top] = (" ".join(label_woorden), bedragen)

            for top in gesorteerd:
                label, bedragen = ontleed[top]
                if (m := _JEUGD.match(label)):
                    sleutel, is_jeugd = m.group(1), True
                elif (m := _TREDE.match(label)):
                    sleutel, is_jeugd = m.group(1), False
                else:
                    continue

                # staan de bedragen op de volgende regel, dan die gebruiken
                if not bedragen:
                    for hoogte in gesorteerd:
                        if 0 < hoogte - top <= _REGELHOOGTE and not ontleed[hoogte][0]:
                            bedragen = ontleed[hoogte][1]
                            break

                for woord in bedragen:
                    letter = min(
                        kolommen, key=lambda l: abs(kolommen[l] - woord["x0"])
                    )
                    waarde = _bedrag(woord["text"])
                    if waarde is None:
                        continue
                    code = f"{sleutel}{letter}" if is_jeugd else f"{letter}{sleutel}"
                    if code in lonen and lonen[code] != waarde:
                        waarschuwingen.append(
                            f"schaal {code} komt meerdere keren voor "
                            f"({lonen[code]} en {waarde}); de eerste is aangehouden"
                        )
                        continue
                    lonen.setdefault(code, waarde)

    if not lonen:
        raise ValueError(
            "geen loontabel herkend in de PDF; verwacht een tabel met de "
            "kolommen B tot en met H"
        )

    definitieve_datum = ingangsdatum or gevonden_datum
    if definitieve_datum is None:
        raise ValueError(
            "geen ingangsdatum gevonden in de PDF; geef die handmatig op"
        )
    if ingangsdatum and gevonden_datum and ingangsdatum != gevonden_datum:
        waarschuwingen.append(
            f"opgegeven ingangsdatum {ingangsdatum:%d-%m-%Y} wijkt af van de datum "
            f"in het document ({gevonden_datum:%d-%m-%Y})"
        )

    return (
        Loontabel(
            naam=naam or f"CAO Glastuinbouw per {definitieve_datum:%d-%m-%Y}",
            ingangsdatum=definitieve_datum,
            lonen=lonen,
        ),
        waarschuwingen,
    )
