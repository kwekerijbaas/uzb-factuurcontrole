"""Beheer van de uitzendkrachtenlijst per uitzendbureau."""

from __future__ import annotations

import uuid
from datetime import date
from urllib.parse import quote
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Gebruiker, huidige_gebruiker
from app.db import get_session
from app.models import Uzk
from app.services.ingest.herkenning import bepaal_uzb
from app.services.ingest.uzk_lijst import lees_uzk_lijst
from app.services.opslag import (
    borg_uzb,
    kaart_op,
    onthoud_uzk,
    uzb_op_sleutel,
    zet_loonschaal,
)
from app.services.tarief import conventies
from app.uploads import EXCEL, lees_upload, leesfouten

from .tarieven import UZB_NAMEN

router = APIRouter(prefix="/uzk", tags=["uzk"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _kaart_van(sessie: Session, uzb_sleutel: str, dag: date):
    """De tariefkaart die vandaag geldt (inclusief handmatige tarieven), om een
    ingevulde schaal te toetsen."""
    return kaart_op(sessie, uzb_sleutel, dag)


def _bekend(sessie: Session) -> list[dict]:
    vandaag = date.today()
    overzicht = []
    for sleutel, naam in UZB_NAMEN.items():
        uzb = uzb_op_sleutel(sessie, sleutel)
        rijen = (
            sessie.scalars(select(Uzk).where(Uzk.uzb_id == uzb.id).order_by(Uzk.naam)).all()
            if uzb
            else []
        )
        kaart = _kaart_van(sessie, sleutel, vandaag) if rijen else None
        # Wie een schaal mist bovenaan: dat zijn de regels die een week
        # blokkeren, dus die moeten als eerste in beeld.
        rijen = sorted(rijen, key=lambda r: (bool(r.loonschaal_code), r.naam.lower()))
        overzicht.append(
            {
                "sleutel": sleutel,
                "naam": naam,
                "aantal": len(rijen),
                "met_schaal": sum(1 for r in rijen if r.loonschaal_code),
                "krachten": rijen,
                # Alle schalen die bij dit bureau al voorkomen, als suggestie
                # bij het invulveld.
                "schalen": sorted({r.loonschaal_code for r in rijen if r.loonschaal_code}),
                "heeft_kaart": kaart is not None,
            }
        )
    return overzicht


@router.get("", response_class=HTMLResponse)
def overzicht(
    request: Request,
    gewijzigd: str = "",
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="uzk.html",
        context={
            "gebruiker": gebruiker,
            "uzbs": UZB_NAMEN,
            "per_uzb": _bekend(sessie),
            "gewijzigd": gewijzigd,
        },
    )


@router.post("/{uzk_id}/loonschaal", response_model=None)
def wijzig_loonschaal(
    uzk_id: uuid.UUID,
    loonschaal: str = Form(...),
    bron: str = Form("handmatig"),
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> Response:
    """Vul de loonschaal van één uitzendkracht met de hand in.

    Nodig omdat SNOOP de schaal niet altijd meelevert, en zonder schaal wordt
    de week niet verwerkt. De ingevoerde schaal wordt getoetst aan de
    tariefkaart die vandaag geldt: een typefout zou anders stilzwijgend een
    bedrag van nul opleveren, precies wat deze controle moet voorkomen.

    `bron` is "handmatig" (de waarde is daarna tegen bestanden beschermd) of
    "bestand" (de gebruiker neemt bewust de bestandswaarde over; de
    bescherming vervalt dan weer).
    """
    kracht = sessie.get(Uzk, uzk_id)
    if kracht is None:
        raise HTTPException(status_code=404, detail="Deze uitzendkracht bestaat niet.")

    waarde = " ".join(loonschaal.split())
    if not waarde:
        raise HTTPException(
            status_code=400, detail="Vul een loonschaal in, bijvoorbeeld 'B2 Flex'."
        )

    uzb_sleutel = kracht.uzb.naam
    kaart = _kaart_van(sessie, uzb_sleutel, date.today())
    if kaart is not None:
        kaartcode = conventies(uzb_sleutel).kaartcode(waarde)
        if kaart.schaal(kaartcode) is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "melding": (
                        f"'{waarde}' hoort niet bij een tarief van "
                        f"{UZB_NAMEN.get(uzb_sleutel, uzb_sleutel)} "
                        f"(dat leest als kaartcode '{kaartcode}'). Kies een "
                        "schaal die op de tariefkaart staat:"
                    ),
                    "punten": sorted(kaart.schalen),
                    "actie": {"tekst": "Terug naar Uitzendkrachten", "href": "/uzk"},
                },
            )

    zet_loonschaal(kracht, waarde, handmatig=bron != "bestand")
    sessie.commit()
    return RedirectResponse(f"/uzk?gewijzigd={quote(kracht.naam)}", status_code=303)


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
    inhoud = await lees_upload(bestand, "uitzendkrachtenlijst", EXCEL)
    with leesfouten("uitzendkrachtenlijst", bestand.filename):
        regels, waarschuwingen = lees_uzk_lijst(inhoud)
        uzb_sleutel = bepaal_uzb(regels, UZB_NAMEN)

    uzb = borg_uzb(sessie, uzb_sleutel, UZB_NAMEN[uzb_sleutel])
    # Een handmatig ingevulde schaal wordt niet stilzwijgend overschreven
    # (`onthoud_uzk` laat hem staan). Wijkt het bestand ervan af, dan wordt dat
    # hier per geval voorgelegd: bestand overnemen, of handmatig laten staan.
    conflicten = []
    for regel in regels:
        rij = onthoud_uzk(sessie, uzb, regel.naam, regel.externe_code, regel.loonschaal)
        if (
            rij.schaal_handmatig
            and regel.loonschaal
            and regel.loonschaal != rij.loonschaal_code
        ):
            conflicten.append(
                {
                    "id": rij.id,
                    "naam": rij.naam,
                    "handmatig": rij.loonschaal_code,
                    "uit_bestand": regel.loonschaal,
                }
            )
    sessie.commit()

    met_schaal = sum(1 for r in regels if r.loonschaal)
    gewisseld = [r for r in regels if r.is_gewisseld]
    zonder = [r.naam for r in regels if not r.loonschaal]
    return templates.TemplateResponse(
        request=request,
        name="uzk_resultaat.html",
        context={
            "gebruiker": gebruiker,
            "titel": f"Uitzendkrachten {UZB_NAMEN[uzb_sleutel]} bijgewerkt",
            "samenvatting": (
                f"{len(regels)} uitzendkrachten ingelezen, {met_schaal} met loonschaal"
                + (f", {len(gewisseld)} gewisseld van schaal." if gewisseld else ".")
                + (
                    f" Vul de schaal van {len(zonder)} uitzendkracht(en) met de "
                    "hand in; zonder schaal wordt een week niet verwerkt."
                    if zonder
                    else ""
                )
            ),
            "waarschuwingen": waarschuwingen,
            "conflicten": conflicten,
        },
    )
