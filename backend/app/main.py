"""UF — UZB-urencontrole (uf.kwekerijbaas.nl)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import Gebruiker, huidige_gebruiker
from app.routers import tarieven, week

BASIS = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASIS / "templates"))

app = FastAPI(
    title="UF — UZB-urencontrole",
    description="Wekelijkse controle van uitzendbureau-uren en -facturen.",
    docs_url=None,
    redoc_url=None,
)

app.include_router(tarieven.router)
app.include_router(week.router)


@app.get("/gezondheid", include_in_schema=False)
def gezondheid() -> dict[str, str]:
    """Voor de health check van App Service; bewust zonder login."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def start(request: Request) -> HTMLResponse:
    gebruiker: Gebruiker = huidige_gebruiker(request)
    return templates.TemplateResponse(
        request=request, name="start.html", context={"gebruiker": gebruiker}
    )
