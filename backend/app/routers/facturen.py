"""Factuurcontrole op een eerder verwerkte week (SPEC §7).

De factuur komt dagen tot weken na de week binnen. Omdat het weekresultaat
bewaard blijft, hoeven SNOOP en Nitea daarvoor niet opnieuw ingelezen te worden
-- en wordt gegarandeerd tegen exact dezelfde berekening vergeleken.
"""

from __future__ import annotations

import base64
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import Gebruiker, huidige_gebruiker
from app.db import get_session
from app.services.export import bestandsnaam_controle, bouw_matchingsbestand
from app.services.factuurcontrole import _LABELS as LABELS
from app.services.factuurcontrole import bevindingenmail, controleer_gesplitst
from app.services.ingest.factuur import Factuur, lees_factuur
from app.services.opslag import (
    bewaarde_weken,
    haal_weekresultaat,
    verwijder_weekresultaat,
)

from app.uploads import PDF, lees_upload, leesfouten

from .tarieven import UZB_NAMEN

router = APIRouter(prefix="/facturen", tags=["facturen"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("", response_class=HTMLResponse)
def overzicht(
    request: Request,
    verwijderd: str = "",
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> HTMLResponse:
    weken = bewaarde_weken(sessie)
    for week in weken:
        week["uzb_naam"] = UZB_NAMEN.get(week["uzb_sleutel"], week["uzb_sleutel"])
    return templates.TemplateResponse(
        request=request,
        name="facturen.html",
        context={"gebruiker": gebruiker, "weken": weken, "verwijderd": verwijderd},
    )


@router.post("/verwijder", response_model=None)
def verwijder_week(
    week: str = Form(...),
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> Response:
    """Verwijder een bewaarde week.

    Nodig wanneer een week onder het verkeerde nummer is verwerkt: opnieuw
    verwerken vervangt alleen hetzelfde nummer, dus het foute resultaat bleef
    anders staan -- en dook op in de weekkeuze van de factuurcontrole.
    """
    try:
        uzb_sleutel, jaar, weeknummer = week.split("|")
        iso_jaar, iso_week = int(jaar), int(weeknummer)
    except ValueError as fout:
        raise HTTPException(status_code=400, detail="ongeldige weekkeuze") from fout

    aantal = verwijder_weekresultaat(sessie, uzb_sleutel, iso_jaar, iso_week)
    sessie.commit()
    if not aantal:
        raise HTTPException(
            status_code=404,
            detail=f"Geen bewaard resultaat voor week {iso_week}/{iso_jaar}.",
        )
    naam = UZB_NAMEN.get(uzb_sleutel, uzb_sleutel)
    return RedirectResponse(
        f"/facturen?verwijderd={iso_week}/{iso_jaar}%20({naam})", status_code=303
    )


@router.post("/controleer", response_class=HTMLResponse)
async def controleer_facturen(
    request: Request,
    week: str = Form(...),
    bestanden: list[UploadFile] = File(default_factory=list),
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> HTMLResponse:
    """Leg de facturen naast een bewaarde week: resultaatscherm met de
    bevindingen en het matchingsbestand, apart voor wie apart gefactureerd
    wordt."""
    try:
        uzb_sleutel, jaar, weeknummer = week.split("|")
        iso_jaar, iso_week = int(jaar), int(weeknummer)
    except ValueError as fout:
        raise HTTPException(status_code=400, detail="ongeldige weekkeuze") from fout

    verwerking = haal_weekresultaat(sessie, uzb_sleutel, iso_jaar, iso_week)
    if verwerking is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Geen bewaard resultaat voor week {iso_week}/{iso_jaar}. "
                "Verwerk die week eerst onder 'Week verwerken'."
            ),
        )

    samen: Factuur | None = None
    for bestand in bestanden:
        if not bestand or not bestand.filename:
            continue
        rauw = await lees_upload(bestand, f"factuur '{bestand.filename}'", PDF)
        with leesfouten(f"factuur '{bestand.filename}'", bestand.filename):
            deel = lees_factuur(rauw, uzb_sleutel)
        for kracht in deel.krachten:
            kracht.factuurnummer = ", ".join(deel.factuurnummers) or None
        if samen is None:
            samen = deel
        else:
            samen.krachten.extend(deel.krachten)
            samen.factuurnummers.extend(
                n for n in deel.factuurnummers if n not in samen.factuurnummers
            )

    if samen is None:
        raise HTTPException(status_code=400, detail="geen factuur meegestuurd")

    naam = UZB_NAMEN.get(uzb_sleutel, uzb_sleutel)
    controles = controleer_gesplitst(verwerking, samen, naam)
    resultaten = []
    for controle in controles:
        mail = bevindingenmail([controle])
        inhoud = bouw_matchingsbestand(controle, mail)
        resultaten.append(
            {
                "controle": controle,
                "label": controle.label or f"Factuurcontrole {naam}",
                "bestandsnaam": bestandsnaam_controle(controle),
                "url": (
                    "data:application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet;base64," + base64.b64encode(inhoud).decode()
                ),
                "mail": mail,
            }
        )
    return templates.TemplateResponse(
        request=request,
        name="facturen_resultaat.html",
        context={
            "gebruiker": gebruiker,
            "uzb_naam": naam,
            "iso_jaar": iso_jaar,
            "iso_week": iso_week,
            "resultaten": resultaten,
            "labels": LABELS,
        },
    )
