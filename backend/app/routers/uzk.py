"""Beheer van de uitzendkrachtenlijst per uitzendbureau."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Gebruiker, huidige_gebruiker
from app.db import get_session
from app.models import Uzk
from app.services.ingest.herkenning import bepaal_uzb
from app.services.ingest.uzk_lijst import lees_uzk_lijst
from app.services.opslag import borg_uzb, onthoud_uzk, uzb_op_sleutel

from .tarieven import UZB_NAMEN

router = APIRouter(prefix="/uzk", tags=["uzk"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _bekend(sessie: Session) -> list[dict]:
    overzicht = []
    for sleutel, naam in UZB_NAMEN.items():
        uzb = uzb_op_sleutel(sessie, sleutel)
        rijen = (
            sessie.scalars(select(Uzk).where(Uzk.uzb_id == uzb.id).order_by(Uzk.naam)).all()
            if uzb
            else []
        )
        overzicht.append(
            {
                "sleutel": sleutel,
                "naam": naam,
                "aantal": len(rijen),
                "met_schaal": sum(1 for r in rijen if r.loonschaal_code),
                "krachten": rijen,
            }
        )
    return overzicht


@router.get("", response_class=HTMLResponse)
def overzicht(
    request: Request,
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="uzk.html",
        context={"gebruiker": gebruiker, "uzbs": UZB_NAMEN, "per_uzb": _bekend(sessie)},
    )


@router.post("/lijst", response_class=HTMLResponse)
async def upload_lijst(
    request: Request,
    bestand: UploadFile = File(...),
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> HTMLResponse:
    """Laad de uitzendkrachtenlijst van één uitzendbureau.

    Het uitzendbureau wordt uit de lijst zelf afgeleid. De lijst vult de
    loonschaal per uitzendkracht, zodat weken waarin SNOOP iemand niet bevat
    toch een tarief krijgen. Bij een schaalwissel telt de meest recente.
    """
    inhoud = await bestand.read()
    try:
        regels, waarschuwingen = lees_uzk_lijst(inhoud)
        uzb_sleutel = bepaal_uzb(regels, UZB_NAMEN)
    except ValueError as fout:
        raise HTTPException(status_code=400, detail=str(fout)) from fout

    uzb = borg_uzb(sessie, uzb_sleutel, UZB_NAMEN[uzb_sleutel])
    for regel in regels:
        onthoud_uzk(sessie, uzb, regel.naam, regel.externe_code, regel.loonschaal)
    sessie.commit()

    met_schaal = sum(1 for r in regels if r.loonschaal)
    gewisseld = [r for r in regels if r.is_gewisseld]
    return templates.TemplateResponse(
        request=request,
        name="tarieven_resultaat.html",
        context={
            "gebruiker": gebruiker,
            "titel": f"Uitzendkrachten {UZB_NAMEN[uzb_sleutel]} bijgewerkt",
            "samenvatting": (
                f"{len(regels)} uitzendkrachten ingelezen, {met_schaal} met loonschaal"
                + (f", {len(gewisseld)} gewisseld van schaal." if gewisseld else ".")
            ),
            "waarschuwingen": waarschuwingen,
            "bevindingen": [],
        },
    )
