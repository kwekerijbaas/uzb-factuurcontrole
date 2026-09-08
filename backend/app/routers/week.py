"""Wekelijkse urencontrole: SNOOP + Nitea inladen en het overzicht downloaden."""

from __future__ import annotations

import base64

from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import Gebruiker, huidige_gebruiker
from app.db import get_session
from app.services.export import bestandsnaam, bouw_overzicht
from app.config import settings
from app.services.ingest import lees_nitea, lees_snoop
from app.services.ingest.herkenning import bepaal_uzb
from app.services.ingest.herkenning import familie_van
from app.services.opslag import (
    apart_gefactureerd,
    bekende_loonschalen,
    bewaar_weekresultaat,
    borg_uzb,
    elders_bekende_uzk,
    handmatige_loonschalen,
    kaart_op,
    onthoud_uzk,
    ruim_oude_weken_op,
)
from app.services.seed.cao_glastuinbouw import cao_toeslag_regels, feestdagen_cao_periode
from app.services.tarief import Kaartreeks, TariefKaart, conventies
from app.services.verwerking import melding_zonder_tarief, verwerk_week
from app.uploads import EXCEL, PDF, lees_upload, leesfouten

from .tarieven import UZB_NAMEN

router = APIRouter(prefix="/week", tags=["week"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def maandag_van(iso_jaar: int, iso_week: int) -> date:
    return date.fromisocalendar(iso_jaar, iso_week, 1)


def bepaal_week(nitea, snoop) -> tuple[int, int]:
    """Leid het weeknummer af uit de bestanden zelf.

    Een getypt weeknummer ging te vaak fout: de week werd dan onder het
    verkeerde nummer bewaard en dook zo op in de factuurcontrole. De datums
    staan gewoon in de bestanden -- Nitea is leidend, en de SNOOP-export moet
    dezelfde week beslaan, anders zijn er bestanden van verschillende weken
    gecombineerd.
    """
    nitea_weken = {
        regel.datum.isocalendar()[:2]
        for medewerker in nitea
        for regel in medewerker.registratie
    }
    if len(nitea_weken) != 1:
        weken = ", ".join(f"{w}/{j}" for j, w in sorted(nitea_weken)) or "geen"
        raise HTTPException(
            status_code=400,
            detail=(
                f"Het Nitea-overzicht beslaat niet precies één week "
                f"(gevonden: {weken}). Exporteer per week één overzicht."
            ),
        )
    iso_jaar, iso_week = next(iter(nitea_weken))

    snoop_weken = {
        regel.datum.isocalendar()[:2]
        for medewerker in snoop
        for regel in medewerker.planning
    }
    if snoop_weken and snoop_weken != {(iso_jaar, iso_week)}:
        anders = ", ".join(
            f"{w}/{j}" for j, w in sorted(snoop_weken - {(iso_jaar, iso_week)})
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"De bestanden horen niet bij elkaar: het Nitea-overzicht is "
                f"van week {iso_week}/{iso_jaar}, de SNOOP-export bevat (ook) "
                f"week {anders}. Kies de SNOOP- en Nitea-bestanden van "
                "dezelfde week."
            ),
        )
    return iso_jaar, iso_week


def kaartreeks_van_week(sessie: Session, uzb_sleutel: str, maandag: date) -> Kaartreeks:
    """De tariefkaart per dag van deze week.

    Een CAO-loontabel gaat in op een vaste datum, niet op een weekgrens: in 2026
    valt 1 juli op woensdag en 1 augustus op zaterdag. De kaart wordt daarom per
    dag bepaald; zolang er niets wijzigt levert dat één periode op, en anders
    lopen de uren van vóór en ná die dag tegen hun eigen tarief.
    """
    periodes: list[tuple[date, TariefKaart | None]] = []
    for verschuiving in range(7):
        dag = maandag + timedelta(days=verschuiving)
        kaart = kaart_op(sessie, uzb_sleutel, dag)
        if not periodes or periodes[-1][1] != kaart:
            periodes.append((dag, kaart))
    return Kaartreeks(tuple(periodes))


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


@router.post("/verwerk", response_class=HTMLResponse)
async def verwerk(
    request: Request,
    snoop_bestand: UploadFile = File(...),
    nitea_bestand: UploadFile = File(...),
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> HTMLResponse:
    """Verwerk één week: resultaatscherm met wie verwerkt is, wie zonder tarief
    staat, en de download van het urenoverzicht.

    Zowel het uitzendbureau als het weeknummer worden uit de bestanden zelf
    afgeleid; een getypt weeknummer ging te vaak fout en zette de week onder
    het verkeerde nummer vast.
    """
    rauwe_snoop = await lees_upload(snoop_bestand, "SNOOP-export", EXCEL)
    rauwe_nitea = await lees_upload(nitea_bestand, "Nitea-overzicht", PDF)

    with leesfouten("SNOOP-export", snoop_bestand.filename):
        snoop = lees_snoop(rauwe_snoop)
        uzb_sleutel = bepaal_uzb(snoop, UZB_NAMEN)
    nitea_opmerkingen: list[str] = []
    with leesfouten("Nitea-overzicht", nitea_bestand.filename):
        nitea = lees_nitea(rauwe_nitea, nitea_opmerkingen)
    if not nitea:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Geen registratieregels herkend in '{nitea_bestand.filename}'. "
                "Verwacht wordt het Nitea-overzicht 'Medewerker uren'; een "
                "factuur of ander rapport heeft die regels niet."
            ),
        )

    iso_jaar, iso_week = bepaal_week(nitea, snoop)
    maandag = maandag_van(iso_jaar, iso_week)
    reeks = kaartreeks_van_week(sessie, uzb_sleutel, maandag)

    uzb = borg_uzb(sessie, uzb_sleutel, UZB_NAMEN[uzb_sleutel])
    verwerking = verwerk_week(
        uzb_sleutel=uzb_sleutel,
        iso_jaar=iso_jaar,
        iso_week=iso_week,
        snoop=snoop,
        nitea=nitea,
        toeslag_regels=cao_toeslag_regels(),
        kaart=reeks,
        conventies=conventies(uzb_sleutel),
        feestdagen=feestdagen_cao_periode(),
        bekende_loonschalen=bekende_loonschalen(sessie, uzb_sleutel),
        handmatige_loonschalen=handmatige_loonschalen(sessie, uzb_sleutel),
        elders_bekend=elders_bekende_uzk(
            sessie, uzb_sleutel, UZB_NAMEN, familie_van(uzb_sleutel)
        ),
        apart_gefactureerd=apart_gefactureerd(sessie, uzb_sleutel),
    )
    if nitea_opmerkingen:
        # Niet-gelezen of anders gelezen Nitea-regels: een stil weggelaten dag
        # is een te laag weektotaal dat niemand opmerkt.
        getoond = nitea_opmerkingen[:12]
        rest = len(nitea_opmerkingen) - len(getoond)
        verwerking.meldingen.append(
            f"Nitea: {len(nitea_opmerkingen)} regel(s) niet of anders gelezen -- "
            "controleer deze dagen in het overzicht: "
            + " | ".join(getoond)
            + (f" | en {rest} meer" if rest > 0 else "")
        )

    # Onthoud iedereen met zijn loonschaal, zodat een week waarin SNOOP
    # onvolledig is alsnog een tarief kan vinden. Wie een schaal mist, hoort
    # juist wél op de uitzendkrachtenlijst te staan, anders is er niets in te
    # vullen.
    for medewerker in verwerking.medewerkers:
        onthoud_uzk(
            sessie, uzb, medewerker.naam, medewerker.nitea_id, medewerker.loonschaal
        )
    for bron in snoop:
        onthoud_uzk(sessie, uzb, bron.naam, None, bron.loonschaal)
    sessie.commit()

    # Bewaar de uitkomst zodat de factuur later los gecontroleerd kan worden.
    bewaar_weekresultaat(sessie, uzb, verwerking)
    ruim_oude_weken_op(
        sessie, settings.bewaartermijn_jaren, behoud=(iso_jaar, iso_week)
    )
    sessie.commit()
    if reeks.is_leeg:
        verwerking.meldingen.insert(
            0,
            "Geen tariefkaart voor deze week: alleen uren berekend, geen bedragen. "
            "Upload eerst een CAO-loontabel en een tariefkaart.",
        )
    elif len(reeks.periodes) > 1:
        verwerking.meldingen.insert(
            0,
            "In deze week gaat een nieuwe loontabel in: "
            + "; ".join(
                f"vanaf {vanaf:%d-%m-%Y} de tarieven van "
                + (f"{kaart.geldig_van:%d-%m-%Y}" if kaart else "(geen kaart)")
                for vanaf, kaart in reeks.periodes
            )
            + ". De uren zijn per dag tegen de dan geldende tarieven afgerekend.",
        )

    # Een ontbrekende schaal of tarief van een enkeling houdt de week niet op:
    # de week is verwerkt en bewaard, maar het overzicht zegt bovenaan wie er
    # zonder bedrag in staat en wat daaraan te doen is.
    if not reeks.is_leeg:
        waarschuwing = melding_zonder_tarief(verwerking)
        if waarschuwing:
            verwerking.meldingen.insert(0, waarschuwing)

    naam = UZB_NAMEN[uzb_sleutel]

    # Wie apart gefactureerd wordt, krijgt een eigen overzicht; het
    # hoofdoverzicht past dan naast de hoofdfactuur.
    hoofd, delen = verwerking.gesplitst()
    downloads = [
        {
            "label": deel.label or f"Weekoverzicht {naam}",
            "bestandsnaam": bestandsnaam(naam, deel),
            "url": _data_url(bouw_overzicht(deel, naam, reeks)),
        }
        for deel in [hoofd, *delen]
    ]

    # Resultaatscherm in plaats van een kale download: wie is verwerkt en wie
    # staat zonder tarief, met de downloads eronder. De bestanden gaan als
    # data-URL mee in de pagina, zodat er niets bewaard hoeft te worden.
    waarschuwing = melding_zonder_tarief(verwerking) if not reeks.is_leeg else None
    return templates.TemplateResponse(
        request=request,
        name="week_resultaat.html",
        context={
            "gebruiker": gebruiker,
            "uzb_naam": naam,
            "verwerking": verwerking,
            "hoofd": hoofd,
            "apart": [d.medewerkers[0] for d in delen],
            "zonder_tarief": verwerking.zonder_tarief,
            # De tabel 'Zonder tarief' zegt het al; de losse meldingen daarover
            # zouden op het scherm dubbel zijn (in het bestand staan ze wel).
            "meldingen": [
                m
                for m in verwerking.meldingen
                if m != waarschuwing
                and not m.endswith(("uur zonder bedrag", "geen bedrag berekend"))
            ],
            "downloads": downloads,
        },
    )


def _data_url(inhoud: bytes) -> str:
    return (
        "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;"
        "base64," + base64.b64encode(inhoud).decode()
    )
