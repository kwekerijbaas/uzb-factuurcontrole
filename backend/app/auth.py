"""Toegang via Supabase Auth (inlogcode per e-mail).

Waarom deze opzet: de app draait niet op Azure, dus Entra ID via App Service
Authentication is geen optie. Supabase Auth stuurt een inlogcode naar het
werk-e-mailadres; wij wisselen die code in voor een sessie en zetten een
ondertekend cookie. Er worden geen wachtwoorden beheerd.

Alleen adressen binnen `toegestane_domeinen` (of expliciet in
`toegestane_emails`) krijgen toegang -- de controle gebeurt zowel vóór het
versturen van de code als na verificatie.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

SESSIE_COOKIE = "uf_sessie"
_TIJDPAD = 60 * 60 * 12  # sessie 12 uur geldig


@dataclass(frozen=True)
class Gebruiker:
    email: str
    id: str | None = None

    @property
    def naam(self) -> str:
        return self.email

    @property
    def is_ontwikkelaar(self) -> bool:
        return self.id is None


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.sessie_geheim, salt="uf-sessie")


def is_toegestaan(email: str) -> bool:
    """Alleen werkadressen van de eigen organisatie mogen inloggen."""
    adres = (email or "").strip().lower()
    if "@" not in adres:
        return False
    if adres in {e.strip().lower() for e in settings.toegestane_emails if e.strip()}:
        return True
    domein = adres.rsplit("@", 1)[1]
    return domein in {d.strip().lower() for d in settings.toegestane_domeinen if d.strip()}


def _supabase(pad: str, payload: dict) -> dict:
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise HTTPException(
            status_code=500,
            detail="Supabase is niet geconfigureerd (SUPABASE_URL / SUPABASE_ANON_KEY).",
        )
    antwoord = httpx.post(
        f"{settings.supabase_url.rstrip('/')}/auth/v1{pad}",
        json=payload,
        headers={
            "apikey": settings.supabase_anon_key,
            "Authorization": f"Bearer {settings.supabase_anon_key}",
        },
        timeout=15,
    )
    if antwoord.status_code >= 400:
        boodschap = ""
        try:
            fout = antwoord.json()
            boodschap = fout.get("msg") or fout.get("error_description") or fout.get("error", "")
        except ValueError:
            boodschap = antwoord.text[:200]
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Supabase Auth gaf een fout: {boodschap or antwoord.status_code}",
        )
    return antwoord.json() if antwoord.content else {}


def stuur_inlogcode(email: str) -> None:
    """Laat Supabase een inlogcode naar dit adres sturen."""
    if not is_toegestaan(email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dit e-mailadres heeft geen toegang tot deze app.",
        )
    _supabase("/otp", {"email": email, "create_user": True})


def verifieer_inlogcode(email: str, code: str) -> Gebruiker:
    """Wissel de gemailde code in voor een gebruiker."""
    if not is_toegestaan(email):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Geen toegang.")
    payload = _supabase("/verify", {"email": email, "token": code, "type": "email"})
    gebruiker = payload.get("user") or {}
    adres = gebruiker.get("email") or email
    if not is_toegestaan(adres):  # dubbele controle na verificatie
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Geen toegang.")
    return Gebruiker(email=adres, id=gebruiker.get("id"))


def zet_sessie(response: Response, gebruiker: Gebruiker) -> None:
    waarde = _serializer().dumps({"email": gebruiker.email, "id": gebruiker.id})
    response.set_cookie(
        SESSIE_COOKIE,
        waarde,
        max_age=_TIJDPAD,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def wis_sessie(response: Response) -> None:
    response.delete_cookie(SESSIE_COOKIE, samesite="lax", secure=settings.cookie_secure)


def gebruiker_uit_cookie(request: Request) -> Gebruiker | None:
    rauw = request.cookies.get(SESSIE_COOKIE)
    if not rauw:
        return None
    try:
        payload = _serializer().loads(rauw, max_age=_TIJDPAD)
    except (BadSignature, SignatureExpired):
        return None
    email = payload.get("email", "")
    if not is_toegestaan(email):  # toegang ingetrokken sinds het inloggen
        return None
    return Gebruiker(email=email, id=payload.get("id"))


def huidige_gebruiker(request: Request) -> Gebruiker:
    if (gebruiker := gebruiker_uit_cookie(request)) is not None:
        return gebruiker
    if not settings.auth_vereist:
        return Gebruiker(email=settings.dev_gebruiker)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Niet ingelogd."
    )
