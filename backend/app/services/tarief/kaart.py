"""Tariefkaart opbouwen uit CAO-lonen x omrekenfactoren (SPEC §6).

De tariefkaart wordt niet los ingevoerd maar afgeleid:

    tarief = CAO-uurloon x omrekenfactor

De omrekenfactor ligt contractueel vast met het uitzendbureau; het CAO-uurloon
verandert periodiek. Het uploaden van een nieuwe CAO-loontabel levert daardoor
vanaf de ingangsdatum automatisch een nieuwe tariefkaart op, zonder dat er
tarieven overgetypt hoeven te worden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .types import SchaalTarief, TariefKaart

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class TariefFactor:
    """Omrekenfactor van één UZB voor één kaartschaal en tariefcategorie."""

    kaartcode: str
    cao_schaal_code: str
    categorie: str
    factor: Decimal


@dataclass
class Loontabel:
    """CAO-loontabel zoals geüpload, geldig vanaf `ingangsdatum`."""

    naam: str
    ingangsdatum: date
    lonen: dict[str, Decimal] = field(default_factory=dict)  # schaal_code -> uurloon

    def loon(self, schaal_code: str) -> Decimal | None:
        return self.lonen.get(schaal_code)


def kies_loontabel(tabellen: list[Loontabel], dag: date) -> Loontabel | None:
    """De loontabel die op `dag` gold: de laatste met ingangsdatum <= dag."""
    geldig = [t for t in tabellen if t.ingangsdatum <= dag]
    return max(geldig, key=lambda t: t.ingangsdatum) if geldig else None


def bouw_tariefkaart(
    uzb_sleutel: str,
    loontabel: Loontabel,
    factoren: list[TariefFactor],
    geldig_tot: date | None = None,
) -> tuple[TariefKaart, list[str]]:
    """Bereken de tariefkaart voor één UZB uit een loontabel en de factoren.

    Retourneert de kaart plus een lijst waarschuwingen voor factoren waarvoor
    de loontabel geen loon bevat (bv. een nieuwe schaal die nog niet in de CAO
    staat) -- die schalen krijgen géén tarief in plaats van een tarief van 0.
    """
    per_kaartcode: dict[str, dict[str, Decimal]] = {}
    waarschuwingen: list[str] = []

    for f in factoren:
        loon = loontabel.loon(f.cao_schaal_code)
        if loon is None:
            waarschuwingen.append(
                f"{f.kaartcode}/{f.categorie}: geen CAO-loon voor schaal "
                f"{f.cao_schaal_code} in loontabel '{loontabel.naam}'"
            )
            continue
        tarief = (loon * f.factor).quantize(_CENT, rounding=ROUND_HALF_UP)
        per_kaartcode.setdefault(f.kaartcode, {})[f.categorie] = tarief

    schalen = {code: SchaalTarief(code, tarieven) for code, tarieven in per_kaartcode.items()}
    kaart = TariefKaart(
        uzb_sleutel=uzb_sleutel,
        geldig_van=loontabel.ingangsdatum,
        geldig_tot=geldig_tot,
        schalen=schalen,
    )
    return kaart, waarschuwingen


def leid_factoren_af(
    kaart: TariefKaart,
    loontabel: Loontabel,
    cao_schaal_van_kaartcode: dict[str, str],
) -> tuple[list[TariefFactor], list[str]]:
    """Bepaal de omrekenfactoren uit een bestaande tariefkaart: factor =
    tarief / uurloon.

    Bedoeld om eenmalig te bootstrappen vanaf de huidige, met de uitzendbureaus
    afgestemde tariefkaart. Daarna zijn alleen nog loontabel-uploads nodig.
    """
    factoren: list[TariefFactor] = []
    waarschuwingen: list[str] = []

    for kaartcode, schaal in sorted(kaart.schalen.items()):
        cao_code = cao_schaal_van_kaartcode.get(kaartcode)
        if cao_code is None:
            waarschuwingen.append(f"{kaartcode}: geen CAO-schaal gekoppeld")
            continue
        loon = loontabel.loon(cao_code)
        if not loon:
            waarschuwingen.append(f"{kaartcode}: geen CAO-loon voor schaal {cao_code}")
            continue
        for categorie, tarief in sorted(schaal.tarieven.items()):
            factoren.append(
                TariefFactor(
                    kaartcode=kaartcode,
                    cao_schaal_code=cao_code,
                    categorie=categorie,
                    factor=(tarief / loon).quantize(Decimal("0.000001")),
                )
            )

    return factoren, waarschuwingen
