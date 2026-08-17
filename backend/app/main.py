"""UF — UZB-urencontrole (uf.kwekerijbaas.nl)."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import gebruiker_uit_cookie
from app.config import settings
from app.routers import facturen, tarieven, toegang, uzk, week

log = logging.getLogger(__name__)

BASIS = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASIS / "templates"))

# Bereikbaar zonder login: de health check en het inlogscherm zelf.
OPEN_PADEN = {"/gezondheid", "/inloggen", "/inloggen/code", "/uitloggen"}

app = FastAPI(
    title="UF — UZB-urencontrole",
    description="Wekelijkse controle van uitzendbureau-uren en -facturen.",
    docs_url=None,
    redoc_url=None,
)

app.include_router(toegang.router)
app.include_router(tarieven.router)
app.include_router(uzk.router)
app.include_router(week.router)
app.include_router(facturen.router)


@app.middleware("http")
async def vereis_login(request: Request, call_next):
    """Sluit de hele app af. Zonder deze laag zou een vergeten dependency op
    één route de loongegevens al openzetten."""
    if request.url.path in OPEN_PADEN or not settings.auth_vereist:
        return await call_next(request)
    if gebruiker_uit_cookie(request) is None:
        return RedirectResponse("/inloggen", status_code=303)
    return await call_next(request)


_TITELS = {
    400: "Bestand niet verwerkt",
    403: "Geen toegang",
    404: "Niet gevonden",
    500: "Er ging iets mis",
}

# Starlette meldt deze in het Engels; de gebruiker leest Nederlands.
_MELDINGEN = {
    403: "Je hebt geen toegang tot deze pagina.",
    404: "Deze pagina bestaat niet.",
}


# De pagina's met een formulier; de verwerk-adressen eronder bestaan alleen als
# doel van dat formulier.
_SECTIES = frozenset({"/week", "/facturen", "/tarieven", "/uzk", "/inloggen"})


def _formulierpagina(pad: str) -> str:
    """De pagina met het formulier dat bij een verwerk-adres hoort.

    `/week/verwerk` hoort bij `/week`, `/tarieven/tariefkaart` bij `/tarieven`.
    Een adres dat nergens bij hoort, wijst terug naar de startpagina.
    """
    sectie = "/" + pad.strip("/").split("/")[0]
    return sectie if sectie in _SECTIES else "/"


def _foutpagina(request: Request, status: int, melding: str, kenmerk: str = "") -> Response:
    """Toon een fout als pagina; alleen niet-browsers krijgen JSON.

    Zonder dit krijgt de gebruiker de kale tekst 'Internal Server Error' of een
    stuk JSON te zien: geen navigatie terug, en geen idee wat er te doen valt.
    """
    if "text/html" not in request.headers.get("accept", ""):
        return JSONResponse({"detail": melding}, status_code=status)
    # Terug naar het formulier zelf, niet naar de vorige pagina: bij een fout op
    # /week/verwerk is de referer datzelfde adres, en dan loopt 'Terug' rond.
    terug = _formulierpagina(request.url.path)
    return templates.TemplateResponse(
        request=request,
        name="fout.html",
        status_code=status,
        context={
            "gebruiker": gebruiker_uit_cookie(request),
            "titel": _TITELS.get(status, "Fout"),
            "melding": melding,
            "kenmerk": kenmerk,
            "terug": terug,
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_fout(request: Request, fout: StarletteHTTPException) -> Response:
    if fout.status_code in (301, 302, 303, 307, 308) or fout.status_code == 401:
        return RedirectResponse("/inloggen", status_code=303)

    # De verwerk-adressen bestaan alleen als doel van een formulier. Komt de
    # browser er met een GET langs -- via de adresbalk, de geschiedenis of een
    # herhaalde download -- dan is dat geen fout maar een omweg: terug naar het
    # formulier is wat de gebruiker bedoelde. Anders eindigt hij op een
    # doodlopende pagina met "Method Not Allowed".
    if fout.status_code == 405 and request.method in ("GET", "HEAD"):
        return RedirectResponse(_formulierpagina(request.url.path), status_code=303)

    melding = _MELDINGEN.get(fout.status_code) or str(fout.detail)
    return _foutpagina(request, fout.status_code, melding)


@app.exception_handler(Exception)
async def onverwachte_fout(request: Request, fout: Exception) -> Response:
    """Vangnet: log met traceback en kenmerk, toon een leesbare pagina.

    Een onverwachte fout hoort zichtbaar te zijn in het log van de hoster, met
    een kenmerk dat de gebruiker kan doorgeven -- anders is er van een melding
    'het werkt niet' niets terug te vinden.
    """
    kenmerk = uuid.uuid4().hex[:8]
    log.exception(
        "onverwachte fout %s bij %s %s", kenmerk, request.method, request.url.path
    )
    return _foutpagina(
        request,
        500,
        "Er is iets misgegaan bij het verwerken. Probeer het opnieuw; blijft het "
        "misgaan, meld dit dan met het kenmerk hieronder.",
        kenmerk,
    )


@app.get("/gezondheid", include_in_schema=False)
def gezondheid() -> dict[str, str]:
    """Voor de health check van de hoster; bewust zonder login."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def start(request: Request) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="start.html",
        context={"gebruiker": gebruiker_uit_cookie(request)},
    )
