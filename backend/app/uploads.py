"""Geüploade bestanden inlezen: type vooraf controleren, leesfouten vertalen.

Het is makkelijk om het verkeerde bestand te kiezen: de SNOOP-export in het
Nitea-veld, een factuur in plaats van een urenoverzicht, of een oud .xls dat
Excel wel opent maar openpyxl niet. De inleespakketten gooien dan een
technische fout -- `zipfile.BadZipFile: File is not a zip file` of
`PDFSyntaxError: No /Root object!` -- die als kale "Internal Server Error" op
het scherm belandt. Dat is precies de melding die niets vertelt over wat er mis
is en waar niemand mee verder kan.

Daarom hier twee dingen: een typecontrole op de eerste bytes van het bestand
(de extensie zegt niets, die is te hernoemen) en een vertaling van elke
leesfout naar een 400 met een zin die het veld noemt. De volledige traceback
gaat naar het log, zodat een echte fout in de inlezer alsnog te vinden is.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass

from fastapi import HTTPException, UploadFile

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Bestandssoort:
    omschrijving: str
    kentekens: tuple[bytes, ...]

    def __or__(self, andere: "Bestandssoort") -> "Bestandssoort":
        """Een veld dat meer dan één formaat aanvaardt, zoals de loontabel:
        de cao-partijen leveren een PDF, wijzelf soms een Excel."""
        return Bestandssoort(
            f"{self.omschrijving} of {andere.omschrijving}",
            self.kentekens + andere.kentekens,
        )


# xlsx is een zip; pdf begint met %PDF-.
EXCEL = Bestandssoort("Excel-bestand (.xlsx)", (b"PK\x03\x04",))
PDF = Bestandssoort("PDF-bestand (.pdf)", (b"%PDF-",))

# Excel 97-2003 (.xls) is een OLE-container en wordt niet gelezen; wel herkend,
# zodat de melding kan vertellen wat de gebruiker moet doen.
_XLS_OUD = b"\xd0\xcf\x11\xe0"


async def lees_upload(bestand: UploadFile | None, veld: str, soort: Bestandssoort) -> bytes:
    """Lees de upload en controleer dat het om het verwachte bestandstype gaat."""
    rauw = await bestand.read() if bestand is not None else b""
    naam = (bestand.filename if bestand else None) or "(zonder naam)"
    if not rauw:
        raise HTTPException(
            status_code=400,
            detail=f"Er is geen bestand gekozen bij '{veld}'.",
        )
    if rauw.startswith(soort.kentekens):
        return rauw

    if soort is EXCEL and rauw.startswith(_XLS_OUD):
        uitleg = (
            "dit is een oud Excel-bestand (.xls). Open het in Excel en sla het "
            "op als 'Excel-werkmap (*.xlsx)'."
        )
    elif rauw.startswith(PDF.kentekens):
        uitleg = "dit is een PDF. Staan de twee bestanden misschien verwisseld?"
    elif rauw.startswith(EXCEL.kentekens):
        uitleg = "dit is een Excel-bestand. Staan de twee bestanden misschien verwisseld?"
    else:
        uitleg = f"er wordt een {soort.omschrijving} verwacht."

    raise HTTPException(
        status_code=400,
        detail=f"'{naam}' bij '{veld}' kan niet worden gelezen: {uitleg}",
    )


@contextmanager
def leesfouten(veld: str, bestandsnaam: str | None = None):
    """Vertaal een fout uit een inlezer naar een leesbare 400.

    `ValueError` is de manier waarop de inlezers zelf melden dat het bestand
    niet klopt; die tekst is al voor de gebruiker geschreven. Andere fouten zijn
    onverwacht -- die worden gelogd met traceback en samengevat, zodat de
    gebruiker verder kan en wij kunnen nakijken wat er speelde.
    """
    naam = bestandsnaam or "het gekozen bestand"
    try:
        yield
    except HTTPException:
        raise
    except ValueError as fout:
        raise HTTPException(status_code=400, detail=f"{veld}: {fout}") from fout
    except Exception as fout:  # noqa: BLE001 - bewust breed; zie docstring
        log.exception("kon %s niet inlezen bij '%s'", naam, veld)
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{naam}' bij '{veld}' kon niet worden gelezen "
                f"({type(fout).__name__}). Controleer of het het juiste bestand "
                "is en of het niet beschadigd is."
            ),
        ) from fout
