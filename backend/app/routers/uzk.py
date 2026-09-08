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
from app.services.ingest.herkenning import herken_uzb, verdeel_per_uzb
from app.services.ingest.uzk_lijst import lees_uzk_lijst
from app.services.opslag import (
    borg_uzb,
    kaart_op,
    onthoud_uzk,
    uzb_op_sleutel,
    verplaats_uzk,
    zet_apart,
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
    behouden: str = "",
    zoek: str = "",
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
            "behouden": behouden,
            # Vooringevuld vanuit het resultaatscherm van een week: de link
            # 'loonschaal invullen' landt dan meteen op de juiste persoon.
            "zoek": zoek,
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
    _toets_schaal(sessie, kracht, waarde)

    zet_loonschaal(kracht, waarde, handmatig=bron != "bestand", door=gebruiker.naam)
    sessie.commit()
    return RedirectResponse(f"/uzk?gewijzigd={quote(kracht.naam)}", status_code=303)


def _toets_schaal(sessie: Session, kracht: Uzk, waarde: str) -> None:
    """Weiger een schaal die niet op de tariefkaart van het bureau staat.

    Past de schaal wél bij een ánder bureau ('D4 SW' bij iemand die onder
    Level One staat), dan is de persoon vrijwel zeker via het Nitea-overzicht
    van een week onder het verkeerde bureau beland. De melding zegt dat, en
    biedt aan hem in één keer te verplaatsen én de schaal op te slaan.
    """
    uzb_sleutel = kracht.uzb.naam
    kaart = _kaart_van(sessie, uzb_sleutel, date.today())
    if kaart is None:
        return
    kaartcode = conventies(uzb_sleutel).kaartcode(waarde)
    if kaart.schaal(kaartcode) is not None:
        return
    bureau = UZB_NAMEN.get(uzb_sleutel, uzb_sleutel)

    class _Regel:  # herken_uzb kijkt naar .loonschaal en .werkgever
        loonschaal = waarde
        werkgever = None

    ander, _ = herken_uzb([_Regel()])
    if ander and ander != uzb_sleutel and ander in UZB_NAMEN:
        ander_naam = UZB_NAMEN[ander]
        raise HTTPException(
            status_code=400,
            detail={
                "melding": (
                    f"'{waarde}' is een schaal van {ander_naam}, maar {kracht.naam} "
                    f"staat hier onder {bureau}. Dat gebeurt als iemand in het "
                    f"Nitea-overzicht van een {bureau}-week voorkwam. Hoort "
                    f"{kracht.naam} bij {ander_naam}, verplaats hem dan; de "
                    "schaal wordt daarbij meteen opgeslagen en een eventuele "
                    f"dubbele rij onder {ander_naam} wordt samengevoegd."
                ),
                "actie": {
                    "tekst": f"Verplaats naar {ander_naam} en sla '{waarde}' op",
                    "href": f"/uzk/{kracht.id}/bureau",
                    "post": {"uzb": ander, "loonschaal": waarde},
                },
            },
        )
    raise HTTPException(
        status_code=400,
        detail={
            "melding": (
                f"'{waarde}' hoort niet bij een tarief van {bureau} (dat leest "
                f"als kaartcode '{kaartcode}'). Kies een schaal die op de "
                "tariefkaart staat:"
            ),
            "punten": sorted(kaart.schalen),
            "actie": {"tekst": "Terug naar Uitzendkrachten", "href": "/uzk"},
        },
    )


@router.post("/{uzk_id}/bureau", response_model=None)
def wijzig_bureau(
    uzk_id: uuid.UUID,
    uzb: str = Form(...),
    loonschaal: str = Form(""),
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> Response:
    """Zet een uitzendkracht onder een ander bureau, eventueel met schaal.

    De weekresultaten gaan mee met de persoon; bestaat de naam daar al, dan
    worden de rijen samengevoegd.
    """
    kracht = sessie.get(Uzk, uzk_id)
    if kracht is None:
        raise HTTPException(status_code=404, detail="Deze uitzendkracht bestaat niet.")
    if uzb not in UZB_NAMEN:
        raise HTTPException(status_code=400, detail=f"Onbekend uitzendbureau '{uzb}'.")
    doel = borg_uzb(sessie, uzb, UZB_NAMEN[uzb])
    rij = verplaats_uzk(sessie, kracht, doel)
    waarde = " ".join(loonschaal.split())
    if waarde:
        _toets_schaal(sessie, rij, waarde)
        zet_loonschaal(rij, waarde, handmatig=True, door=gebruiker.naam)
    sessie.commit()
    return RedirectResponse(
        f"/uzk?gewijzigd={quote(rij.naam)}&zoek={quote(rij.naam)}", status_code=303
    )


@router.post("/{uzk_id}/apart", response_model=None)
def wijzig_apart(
    uzk_id: uuid.UUID,
    apart: str = Form("nee"),
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> Response:
    """Markeer een uitzendkracht als apart gefactureerd (of juist niet).

    Zo iemand krijgt bij het verwerken een eigen overzicht en bij de
    factuurcontrole een eigen controle; het hoofdoverzicht blijft dan naast
    de hoofdfactuur passen.
    """
    kracht = sessie.get(Uzk, uzk_id)
    if kracht is None:
        raise HTTPException(status_code=404, detail="Deze uitzendkracht bestaat niet.")
    zet_apart(kracht, apart == "ja", door=gebruiker.naam)
    sessie.commit()
    return RedirectResponse(
        f"/uzk?gewijzigd={quote(kracht.naam)}&zoek={quote(kracht.naam)}", status_code=303
    )


@router.post("/{uzk_id}/loonschaal/behoud", response_model=None)
def behoud_loonschaal(
    uzk_id: uuid.UUID,
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> Response:
    """De expliciete 'nee' op de vraag of het bestand de handmatige schaal mag
    overschrijven. Wijzigt niets; bevestigt alleen dat de handmatige waarde
    blijft staan (en bij een volgende upload met hetzelfde verschil wordt het
    opnieuw gevraagd)."""
    kracht = sessie.get(Uzk, uzk_id)
    if kracht is None:
        raise HTTPException(status_code=404, detail="Deze uitzendkracht bestaat niet.")
    return RedirectResponse(f"/uzk?behouden={quote(kracht.naam)}", status_code=303)


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
        # Eén lijst mag alle bureaus bevatten; per regel staat het bureau erbij.
        per_uzb = verdeel_per_uzb(regels, UZB_NAMEN)

    samenvattingen = []
    conflicten = []
    for uzb_sleutel, groep in per_uzb.items():
        uzb = borg_uzb(sessie, uzb_sleutel, UZB_NAMEN[uzb_sleutel])
        # Een handmatig ingevulde schaal wordt niet stilzwijgend overschreven
        # (`onthoud_uzk` laat hem staan). Wijkt het bestand ervan af, dan wordt
        # dat per geval voorgelegd: bestand overnemen, of handmatig laten staan.
        for regel in groep:
            rij = onthoud_uzk(
                sessie, uzb, regel.naam, regel.externe_code, regel.loonschaal
            )
            if (
                rij.schaal_handmatig
                and regel.loonschaal
                and regel.loonschaal != rij.loonschaal_code
            ):
                conflicten.append(
                    {
                        "id": rij.id,
                        "uzb_naam": UZB_NAMEN[uzb_sleutel],
                        "naam": rij.naam,
                        "handmatig": rij.loonschaal_code,
                        "uit_bestand": regel.loonschaal,
                        "door": rij.schaal_door,
                    }
                )
        met_schaal = sum(1 for r in groep if r.loonschaal)
        gewisseld = sum(1 for r in groep if r.is_gewisseld)
        zonder = [r.naam for r in groep if not r.loonschaal]
        samenvattingen.append(
            f"{UZB_NAMEN[uzb_sleutel]}: {len(groep)} uitzendkrachten, "
            f"{met_schaal} met loonschaal"
            + (f", {gewisseld} gewisseld van schaal" if gewisseld else "")
            + (
                f". Zonder schaal (met de hand invullen): {', '.join(sorted(zonder))}"
                if zonder
                else "."
            )
        )
    sessie.commit()

    return templates.TemplateResponse(
        request=request,
        name="uzk_resultaat.html",
        context={
            "gebruiker": gebruiker,
            "titel": "Uitzendkrachtenlijst verwerkt",
            "samenvatting": (
                f"{len(regels)} uitzendkrachten ingelezen voor "
                f"{len(per_uzb)} uitzendbureau(s)."
            ),
            "samenvattingen": samenvattingen,
            "waarschuwingen": waarschuwingen,
            "conflicten": conflicten,
        },
    )
