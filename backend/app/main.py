"""UF — UZB-urencontrole (uf.kwekerijbaas.nl)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.auth import gebruiker_uit_cookie
from app.config import settings
from app.routers import facturen, tarieven, toegang, uzk, week

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
