"""Wekelijkse urencontrole: SNOOP + Nitea inladen en het overzicht downloaden."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import Gebruiker, huidige_gebruiker
from app.db import get_session
from app.services.export import bestandsnaam, bouw_overzicht
from app.config import settings
from app.services.ingest import lees_nitea, lees_snoop
from app.services.ingest.herkenning import bepaal_uzb
from app.services.opslag import (
    bekende_loonschalen,
    bewaar_weekresultaat,
    borg_uzb,
    factoren_op,
    loontabel_op,
    onthoud_uzk,
    ruim_oude_weken_op,
)
from app.services.seed.cao_glastuinbouw import cao_toeslag_regels, feestdagen_cao_periode
from app.services.tarief import bouw_tariefkaart, conventies
from app.services.verwerking import verwerk_week
from app.uploads import EXCEL, PDF, lees_upload, leesfouten

from .tarieven import UZB_NAMEN

router = APIRouter(prefix="/week", tags=["week"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

# Het jeugd-payroll-tabblad hoort bij de Level One-conventies.
CONVENTIE_SLEUTEL = {"L1": "L1", "L1_JEUGD": "L1", "SW": "SW", "CK": "CK"}


def maandag_van(iso_jaar: int, iso_week: int) -> date:
    return date.fromisocalendar(iso_jaar, iso_week, 1)


@router.get("", response_class=HTMLResponse)
def formulier(
    request: Request, gebruiker: Gebruiker = Depends(huidige_gebruiker)
) -> HTMLResponse:
    vandaag = date.today()
    jaar, week, _ = vandaag.isocalendar()
    return templates.TemplateResponse(
        request=request,
        name="week.html",
        context={
            "gebruiker": gebruiker,
            "uzbs": UZB_NAMEN,
            "iso_jaar": jaar,
            "iso_week": max(week - 1, 1),  # meestal controleer je de vorige week
        },
    )


@router.post("/verwerk")
async def verwerk(
    iso_jaar: int = Form(...),
    iso_week: int = Form(...),
    snoop_bestand: UploadFile = File(...),
    nitea_bestand: UploadFile = File(...),
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> Response:
    """Verwerk één week en geef het urenoverzicht als download terug.

    Het uitzendbureau wordt uit de SNOOP-export afgeleid; die noteert het in de
    kolom "Werkgever op datum shift".
    """
    try:
        maandag = maandag_van(iso_jaar, iso_week)
    except ValueError as fout:
        raise HTTPException(status_code=400, detail=f"ongeldige week: {fout}") from fout

    rauwe_snoop = await lees_upload(snoop_bestand, "SNOOP-export", EXCEL)
    rauwe_nitea = await lees_upload(nitea_bestand, "Nitea-overzicht", PDF)

    with leesfouten("SNOOP-export", snoop_bestand.filename):
        snoop = lees_snoop(rauwe_snoop)
        uzb_sleutel = bepaal_uzb(snoop, UZB_NAMEN)
    with leesfouten("Nitea-overzicht", nitea_bestand.filename):
        nitea = lees_nitea(rauwe_nitea)
    if not nitea:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Geen registratieregels herkend in '{nitea_bestand.filename}'. "
                "Verwacht wordt het Nitea-overzicht 'Medewerker uren'; een "
                "factuur of ander rapport heeft die regels niet."
            ),
        )

    lonen = loontabel_op(sessie, maandag)
    factoren = factoren_op(sessie, uzb_sleutel, maandag)
    kaart = None
    if lonen and factoren:
        kaart, _ = bouw_tariefkaart(uzb_sleutel, lonen, factoren)

    uzb = borg_uzb(sessie, uzb_sleutel, UZB_NAMEN[uzb_sleutel])
    verwerking = verwerk_week(
        uzb_sleutel=uzb_sleutel,
        iso_jaar=iso_jaar,
        iso_week=iso_week,
        snoop=snoop,
        nitea=nitea,
        toeslag_regels=cao_toeslag_regels(),
        kaart=kaart,
        conventies=conventies(CONVENTIE_SLEUTEL[uzb_sleutel]),
        feestdagen=feestdagen_cao_periode(),
        bekende_loonschalen=bekende_loonschalen(sessie, uzb_sleutel),
    )

    # Onthoud iedereen met zijn loonschaal, zodat een week waarin SNOOP
    # onvolledig is alsnog een tarief kan vinden.
    for medewerker in verwerking.medewerkers:
        onthoud_uzk(
            sessie, uzb, medewerker.naam, medewerker.nitea_id, medewerker.loonschaal
        )
    for bron in snoop:
        onthoud_uzk(sessie, uzb, bron.naam, None, bron.loonschaal)

    # Bewaar de uitkomst zodat de factuur later los gecontroleerd kan worden.
    bewaar_weekresultaat(sessie, uzb, verwerking)
    ruim_oude_weken_op(
        sessie, settings.bewaartermijn_jaren, behoud=(iso_jaar, iso_week)
    )
    sessie.commit()
    if kaart is None:
        verwerking.meldingen.insert(
            0,
            "Geen tariefkaart voor deze week: alleen uren berekend, geen bedragen. "
            "Upload eerst een CAO-loontabel en een tariefkaart.",
        )

    naam = UZB_NAMEN[uzb_sleutel]

    inhoud = bouw_overzicht(verwerking, naam, kaart)
    return Response(
        content=inhoud,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{bestandsnaam(naam, verwerking)}"'
        },
    )
