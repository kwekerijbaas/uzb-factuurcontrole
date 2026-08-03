"""Toegang via Azure App Service Authentication ("Easy Auth").

Azure handelt de Entra ID-login af vóór de request de app bereikt en zet de
identiteit in request-headers. De app doet zelf geen tokenvalidatie en beheert
geen wachtwoorden; wie toegang heeft regel je in Entra met een
beveiligingsgroep.

Lokaal staat Easy Auth niet aan. Zet dan `auth_vereist=false` in `.env`; er
wordt dan met `dev_gebruiker` gewerkt. In Azure moet `auth_vereist` aan blijven,
anders zou de app zonder login bereikbaar zijn.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status

from app.config import settings

_HEADER_NAAM = "x-ms-client-principal-name"
_HEADER_ID = "x-ms-client-principal-id"
_HEADER_PRINCIPAL = "x-ms-client-principal"


@dataclass(frozen=True)
class Gebruiker:
    naam: str
    id: str | None = None
    rollen: tuple[str, ...] = ()

    @property
    def is_ontwikkelaar(self) -> bool:
        return self.id is None


def _rollen_uit_principal(waarde: str) -> tuple[str, ...]:
    """Lees de rol-claims uit de base64-JSON die Easy Auth meestuurt."""
    try:
        payload = json.loads(base64.b64decode(waarde).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return ()
    rol_type = payload.get("claims_typ_rol") or "roles"
    rollen = {
        claim.get("val")
        for claim in payload.get("claims", [])
        if claim.get("typ") in (rol_type, "roles") and claim.get("val")
    }
    return tuple(sorted(rollen))


def huidige_gebruiker(request: Request) -> Gebruiker:
    naam = request.headers.get(_HEADER_NAAM)
    if naam:
        principal = request.headers.get(_HEADER_PRINCIPAL, "")
        return Gebruiker(
            naam=naam,
            id=request.headers.get(_HEADER_ID),
            rollen=_rollen_uit_principal(principal) if principal else (),
        )

    if settings.auth_vereist:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Geen ingelogde gebruiker. Deze app hoort achter Azure App Service "
                "Authentication (Entra ID) te draaien."
            ),
        )
    return Gebruiker(naam=settings.dev_gebruiker)


VereistIngelogd = Depends(huidige_gebruiker)
