"""Beheer van CAO-loontabellen en UZB-tariefkaarten (SPEC §6)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import Gebruiker, huidige_gebruiker
from app.config import settings
from app.db import get_session
from app.services.ingest import cao_schaal_code, lees_cao_loontabel, lees_tariefkaart
from app.services.ingest.loontabel import lees_loontabel
from app.services.opslag import (
    bewaar_factoren,
    bewaar_loontabel,
    borg_uzb,
    factoren_op,
    loontabel_op,
    loontabellen,
)
from app.services.tarief import (
    SchaalTarief,
    TariefKaart,
    leid_factoren_af,
    valideer_minimumloon,
    valideer_tarieven,
    valideer_uniforme_factor,
    vergelijk_factoren,
)
from app.services.tarief.uzb import CONVENTIES

router = APIRouter(prefix="/tarieven", tags=["tarieven"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

UZB_NAMEN = {
    "L1": "Level One",
    "L1_JEUGD": "Level One jeugd-payroll",
    "SW": "Sterk Werk",
    "CK": "Cervokordaat",
}


@router.get("", response_class=HTMLResponse)
def overzicht(
    request: Request,
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> HTMLResponse:
    tabellen = loontabellen(sessie)
    vandaag = date.today()
    per_uzb = [
        {
            "sleutel": sleutel,
            "naam": naam,
            "aantal_factoren": len(factoren_op(sessie, sleutel, vandaag)),
        }
        for sleutel, naam in UZB_NAMEN.items()
    ]
    return templates.TemplateResponse(
        request=request,
        name="tarieven.html",
        context={
            "gebruiker": gebruiker,
            "loontabellen": tabellen,
            "per_uzb": per_uzb,
            "actieve_loontabel": loontabel_op(sessie, vandaag),
        },
    )


@router.post("/loontabel", response_class=HTMLResponse)
async def upload_loontabel(
    request: Request,
    bestand: UploadFile = File(...),
    naam: str = Form(...),
    ingangsdatum: date = Form(...),
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> HTMLResponse:
    """Nieuwe CAO-lonen. De omrekenfactoren blijven staan; de tarieven bewegen
    vanaf de ingangsdatum automatisch mee."""
    inhoud = await bestand.read()
    try:
        tabel, waarschuwingen = lees_loontabel(inhoud, naam, ingangsdatum)
    except ValueError as fout:
        raise HTTPException(status_code=400, detail=str(fout)) from fout

    bevindingen = valideer_minimumloon(tabel, Decimal(settings.minimumloon))
    bewaar_loontabel(sessie, tabel, bron_bestand=bestand.filename)
    sessie.commit()

    return templates.TemplateResponse(
        request=request,
        name="tarieven_resultaat.html",
        context={
            "gebruiker": gebruiker,
            "titel": f"Loontabel '{naam}' opgeslagen",
            "samenvatting": (
                f"{len(tabel.lonen)} CAO-schalen, geldig vanaf "
                f"{ingangsdatum:%d-%m-%Y}."
            ),
            "waarschuwingen": waarschuwingen,
            "bevindingen": bevindingen,
        },
    )


@router.post("/tariefkaart", response_class=HTMLResponse)
async def upload_tariefkaart(
    request: Request,
    bestand: UploadFile = File(...),
    ingangsdatum: date = Form(...),
    ook_lonen: bool = Form(False),
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> HTMLResponse:
    """Nieuwe tariefkaart van de uitzendbureaus.

    De omrekenfactoren worden afgeleid (`factor = tarief / CAO-uurloon`) en per
    uitzendbureau vergeleken met de vorige versie, zodat een onbedoelde
    wijziging opvalt.
    """
    inhoud = await bestand.read()
    try:
        bladen, waarschuwingen = lees_tariefkaart(inhoud)
    except ValueError as fout:
        raise HTTPException(status_code=400, detail=str(fout)) from fout

    if ook_lonen:
        tabel, loon_waarschuwingen = lees_cao_loontabel(
            inhoud, f"CAO-lonen bij tariefkaart {ingangsdatum:%d-%m-%Y}", ingangsdatum
        )
        waarschuwingen += loon_waarschuwingen
        bewaar_loontabel(sessie, tabel, bron_bestand=bestand.filename)
        sessie.flush()

    lonen = loontabel_op(sessie, ingangsdatum)
    if lonen is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Geen CAO-loontabel die geldt op deze ingangsdatum. Upload eerst "
                "de loontabel, of vink 'lonen uit dit bestand overnemen' aan."
            ),
        )

    bevindingen = []
    resultaten = []
    for sleutel, blad in bladen.items():
        bevindingen += valideer_tarieven(sleutel, blad.tarieven)

        kaart = TariefKaart(
            uzb_sleutel=sleutel,
            geldig_van=ingangsdatum,
            schalen={c: SchaalTarief(c, t) for c, t in blad.tarieven.items()},
        )
        koppeling = {c: cao_schaal_code(c) for c in blad.tarieven}
        koppeling = {c: v for c, v in koppeling.items() if v}
        factoren, zonder_loon = leid_factoren_af(kaart, lonen, koppeling)

        if blad.uniforme_factor:
            bevindingen += valideer_uniforme_factor(sleutel, factoren)

        uzb = borg_uzb(sessie, sleutel, UZB_NAMEN.get(sleutel))
        verschillen = vergelijk_factoren(
            sleutel, factoren_op(sessie, sleutel, ingangsdatum), factoren
        )
        bewaar_factoren(sessie, uzb, factoren, ingangsdatum)

        resultaten.append(
            {
                "sleutel": sleutel,
                "naam": UZB_NAMEN.get(sleutel, sleutel),
                "schalen": len(blad.tarieven),
                "factoren": len(factoren),
                "zonder_loon": zonder_loon,
                "verschillen": verschillen,
            }
        )

    sessie.commit()
    return templates.TemplateResponse(
        request=request,
        name="tarieven_resultaat.html",
        context={
            "gebruiker": gebruiker,
            "titel": f"Tariefkaart verwerkt per {ingangsdatum:%d-%m-%Y}",
            "samenvatting": (
                f"{sum(r['factoren'] for r in resultaten)} omrekenfactoren afgeleid "
                f"voor {len(resultaten)} uitzendbureaus."
            ),
            "waarschuwingen": waarschuwingen,
            "bevindingen": bevindingen,
            "resultaten": resultaten,
        },
    )
