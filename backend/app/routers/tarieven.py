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
from app.services.ingest.cao_pdf import lees_cao_pdf
from app.services.ingest.level_one import lees_level_one_export
from app.services.ingest.loontabel import lees_loontabel
from app.services.tarief.kaart import Loontabel
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
from app.uploads import EXCEL, PDF, lees_upload, leesfouten

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
    naam: str = Form(""),
    ingangsdatum: date | None = Form(None),
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> HTMLResponse:
    """Nieuwe CAO-lonen. De omrekenfactoren blijven staan; de tarieven bewegen
    vanaf de ingangsdatum automatisch mee.

    De cao-partijen publiceren de loontabel als PDF; een Excel-bestand met een
    schaal- en een loonkolom kan ook. Bij een PDF worden de omschrijving en de
    ingangsdatum uit het document gehaald als ze niet zijn ingevuld.
    """
    inhoud = await lees_upload(bestand, "loontabel", PDF | EXCEL)
    is_pdf = inhoud.startswith(PDF.kentekens)
    with leesfouten("loontabel", bestand.filename):
        if is_pdf:
            tabel, waarschuwingen = lees_cao_pdf(inhoud, naam or None, ingangsdatum)
        else:
            if ingangsdatum is None:
                raise ValueError(
                    "geef een ingangsdatum op; die staat niet in een Excel-bestand"
                )
            tabel, waarschuwingen = lees_loontabel(
                inhoud, naam or bestand.filename or "CAO-loontabel", ingangsdatum
            )
    ingangsdatum = tabel.ingangsdatum

    bewaar_loontabel(sessie, tabel, bron_bestand=bestand.filename)
    sessie.flush()
    # Toets de lonen zoals ze na deze upload gelden, niet alleen de geüploade
    # tabel: een tabel overschrijft alleen de schalen die hij noemt, dus een
    # fout in een oudere tabel blijft anders onzichtbaar.
    geldend = loontabel_op(sessie, ingangsdatum) or tabel
    bevindingen = valideer_minimumloon(geldend, Decimal(settings.minimumloon))
    sessie.commit()

    overgenomen = len(geldend.lonen) - len(tabel.lonen)
    return templates.TemplateResponse(
        request=request,
        name="tarieven_resultaat.html",
        context={
            "gebruiker": gebruiker,
            "titel": f"Loontabel '{tabel.naam}' opgeslagen",
            "samenvatting": (
                f"{len(tabel.lonen)} CAO-schalen, geldig vanaf "
                f"{ingangsdatum:%d-%m-%Y}."
                + (
                    f" Nog {overgenomen} schalen houden hun loon uit een eerdere "
                    "tabel; samen gelden er "
                    f"{len(geldend.lonen)}."
                    if overgenomen > 0
                    else ""
                )
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
    inhoud = await lees_upload(bestand, "tariefkaart", EXCEL)
    with leesfouten("tariefkaart", bestand.filename):
        bladen, waarschuwingen = lees_tariefkaart(inhoud)

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


@router.post("/level-one", response_class=HTMLResponse)
async def upload_level_one(
    request: Request,
    bestand: UploadFile = File(...),
    ingangsdatum: date | None = Form(None),
    kolom: str = Form("nieuw"),
    ook_lonen: bool = Form(False),
    sessie: Session = Depends(get_session),
    gebruiker: Gebruiker = Depends(huidige_gebruiker),
) -> HTMLResponse:
    """Level One's eigen tariefexport verwerken.

    Dit formaat wijkt af van de geconsolideerde kaart: één blok per CAO-schaal
    met een regel per loonbestanddeel, en aparte kolommen voor Vast, Flex en
    Seizoen. Alleen de tarieven van Level One worden bijgewerkt; de andere
    uitzendbureaus blijven ongemoeid.
    """
    inhoud = await lees_upload(bestand, "Level One-export", EXCEL)
    with leesfouten("Level One-export", bestand.filename):
        export, waarschuwingen = lees_level_one_export(inhoud, kolom)

    # De ingangsdatum staat in de kolomkop ("Loon per 1/7/26"); die hoeft dus
    # niet overgetypt te worden. Een ingevulde datum gaat wel voor, voor het
    # geval de kop hem niet noemt of niet klopt.
    ingangsdatum = ingangsdatum or export.ingangsdatum
    if ingangsdatum is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Geen ingangsdatum gevonden. Die staat normaal in de kolomkop "
                "(bijvoorbeeld 'Loon per 1/7/26'); vul hem anders zelf in."
            ),
        )

    if ook_lonen and export.lonen:
        tabel = Loontabel(
            naam=f"CAO-lonen bij Level One-export {ingangsdatum:%d-%m-%Y}",
            ingangsdatum=ingangsdatum,
            lonen=dict(export.lonen),
        )
        bewaar_loontabel(sessie, tabel, bron_bestand=bestand.filename)
        sessie.flush()

    lonen = loontabel_op(sessie, ingangsdatum)
    if lonen is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Geen CAO-loontabel die geldt op deze ingangsdatum. Vink "
                "'lonen uit dit bestand overnemen' aan, of upload eerst een loontabel."
            ),
        )

    bevindingen = valideer_tarieven("L1", export.tarieven)
    # Ook hier de lonen toetsen zoals ze na deze upload gelden: de export noemt
    # alleen de schalen van Level One, de rest komt uit een eerdere tabel.
    bevindingen += valideer_minimumloon(lonen, Decimal(settings.minimumloon))
    kaart = TariefKaart(
        uzb_sleutel="L1",
        geldig_van=ingangsdatum,
        schalen={c: SchaalTarief(c, t) for c, t in export.tarieven.items()},
    )
    koppeling = {c: cao_schaal_code(c) for c in export.tarieven}
    koppeling = {c: v for c, v in koppeling.items() if v}
    factoren, zonder_loon = leid_factoren_af(kaart, lonen, koppeling)

    uzb = borg_uzb(sessie, "L1", UZB_NAMEN["L1"])
    # De export bevat soms alleen de gewijzigde schalen; alles afsluiten zou de
    # rest vanaf deze datum zonder tarief zetten. Vergelijken gebeurt met de
    # factoren zoals ze na de upload gelden, zodat de ongemoeide schalen niet
    # ten onrechte als 'vervallen' in het verschiloverzicht komen.
    oude_factoren = factoren_op(sessie, "L1", ingangsdatum)
    bewaar_factoren(sessie, uzb, factoren, ingangsdatum, volledig=False)
    sessie.flush()
    verschillen = vergelijk_factoren(
        "L1", oude_factoren, factoren_op(sessie, "L1", ingangsdatum)
    )
    sessie.commit()

    return templates.TemplateResponse(
        request=request,
        name="tarieven_resultaat.html",
        context={
            "gebruiker": gebruiker,
            "titel": f"Level One-tarieven verwerkt per {ingangsdatum:%d-%m-%Y}",
            "samenvatting": (
                f"{len(export.tarieven)} schalen ingelezen, {len(factoren)} "
                "omrekenfactoren afgeleid."
            ),
            "waarschuwingen": waarschuwingen,
            "bevindingen": bevindingen,
            "resultaten": [
                {
                    "sleutel": "L1",
                    "naam": UZB_NAMEN["L1"],
                    "schalen": len(export.tarieven),
                    "factoren": len(factoren),
                    "zonder_loon": zonder_loon,
                    "verschillen": verschillen,
                }
            ],
        },
    )
