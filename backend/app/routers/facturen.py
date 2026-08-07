"""Factuurcontrole op een eerder verwerkte week (SPEC §7).

De factuur komt dagen tot weken na de week binnen. Omdat het weekresultaat
bewaard blijft, hoeven SNOOP en Nitea daarvoor niet opnieuw ingelezen te worden
-- en wordt gegarandeerd tegen exact dezelfde berekening vergeleken.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import Gebruiker, huidige_gebruiker
from app.db import get_session
from app.services.export import bouw_matchingsbestand
from app.services.factuurcontrole import bevindingenmail, controleer
from app.services.ingest.factuur import Factuur, lees_factuur
from app.services.opslag import bewaarde_weken, haal_weekresultaat

from .tarieven import UZB_NAMEN

router = APIRouter(prefix="/facturen", tags=["facturen"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("", response_class=HTMLResponse)
def overzicht(
    request: Request,
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> HTMLResponse:
    weken = bewaarde_weken(sessie)
    for week in weken:
        week["uzb_naam"] = UZB_NAMEN.get(week["uzb_sleutel"], week["uzb_sleutel"])
    return templates.TemplateResponse(
        request=request,
        name="facturen.html",
        context={"gebruiker": gebruiker, "weken": weken},
    )


@router.post("/controleer")
async def controleer_facturen(
    week: str = Form(...),
    bestanden: list[UploadFile] = File(default_factory=list),
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> Response:
    """Leg de facturen naast een bewaarde week en geef het matchingsbestand."""
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
        rauw = await bestand.read()
        if not rauw:
            continue
        try:
            deel = lees_factuur(rauw, uzb_sleutel)
        except ValueError as fout:
            raise HTTPException(
                status_code=400, detail=f"{bestand.filename}: {fout}"
            ) from fout
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
    controle = controleer(verwerking, samen, naam)
    inhoud = bouw_matchingsbestand(controle, bevindingenmail([controle]))

    veilig = "".join(c if c.isalnum() else "_" for c in naam).strip("_")
    bestandsnaam = f"Factuurcontrole_{veilig}_week_{iso_week}_{iso_jaar}.xlsx"
    return Response(
        content=inhoud,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{bestandsnaam}"'},
    )
