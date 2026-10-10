"""In- en uitloggen met een code per e-mail (Supabase Auth)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import (
    stuur_inlogcode,
    verifieer_inlogcode,
    wis_sessie,
    zet_sessie,
)

router = APIRouter(tags=["toegang"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _scherm(
    request: Request,
    email: str = "",
    stap: str = "email",
    melding: str | None = None,
    fout: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="inloggen.html",
        context={"email": email, "stap": stap, "melding": melding, "fout": fout},
        status_code=status_code,
    )


@router.get("/inloggen", response_class=HTMLResponse)
def inlogscherm(request: Request) -> HTMLResponse:
    return _scherm(request)


@router.post("/inloggen", response_class=HTMLResponse)
def vraag_code(request: Request, email: str = Form(...)) -> HTMLResponse:
    try:
        stuur_inlogcode(email)
    except HTTPException as fout:
        return _scherm(request, email=email, fout=fout.detail, status_code=fout.status_code)
    return _scherm(
        request,
        email=email,
        stap="code",
        melding=f"We hebben een inlogcode gestuurd naar {email}.",
    )


@router.post("/inloggen/code", response_model=None)
def controleer_code(
    request: Request, email: str = Form(...), code: str = Form(...)
) -> HTMLResponse | RedirectResponse:
    try:
        gebruiker = verifieer_inlogcode(email, code.strip())
    except HTTPException as fout:
        return _scherm(
            request,
            email=email,
            stap="code",
            fout=f"{fout.detail} Controleer de code of vraag een nieuwe aan.",
            status_code=fout.status_code,
        )
    antwoord = RedirectResponse("/", status_code=303)
    zet_sessie(antwoord, gebruiker)
    return antwoord


@router.get("/uitloggen")
def uitloggen() -> RedirectResponse:
    antwoord = RedirectResponse("/inloggen", status_code=303)
    wis_sessie(antwoord)
    return antwoord
