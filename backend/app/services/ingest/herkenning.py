"""Controleren dat een geüpload bestand bij het gekozen uitzendbureau hoort.

Zonder deze controle kan iemand Level One kiezen en de bestanden van Sterk Werk
uploaden; de app rekent dan de uren van Sterk Werk af tegen de tarieven van
Level One en bewaart dat ook nog als weekresultaat. Dat levert een overzicht op
dat er compleet uitziet maar volledig onjuist is.

SNOOP noteert het bureau in de kolom "Werkgever op datum shift"; de loonschaal
verraadt het daarnaast in het achtervoegsel ("B2 Flex" tegenover "B2 Sw").
"""

from __future__ import annotations

import re
from collections import Counter

# genormaliseerde werkgeversnaam -> UZB-sleutel
_WERKGEVERS = {
    "levelone": "L1",
    "levelonejeugd": "L1_JEUGD",
    "levelonepayroll": "L1_JEUGD",
    "sterkwerk": "SW",
    "cervokordaat": "CK",
}

# achtervoegsel van de loonschaal -> UZB-sleutel
_SUFFIXEN = {
    "sw": "SW",
    "flex": "L1",
    "vast": "L1",
    "seizoens": "L1",
    "seizoenskrachten": "L1",
    "payroll": "L1_JEUGD",
    "jeugd": "L1_JEUGD",
}

# uitzendbureaus die dezelfde bestanden mogen delen
_FAMILIE = {"L1": {"L1", "L1_JEUGD"}, "L1_JEUGD": {"L1", "L1_JEUGD"}}


def _norm(waarde: str | None) -> str:
    return re.sub(r"[^a-z]", "", str(waarde or "").lower())


def herken_uzb(medewerkers) -> tuple[str | None, str | None]:
    """Bepaal uit welke bron dit bestand komt.

    Retourneert (sleutel, gevonden naam). De werkgeverskolom telt het zwaarst;
    zonder die kolom wordt op het achtervoegsel van de loonschaal teruggevallen.
    """
    werkgevers = Counter(
        str(m.werkgever).strip() for m in medewerkers if getattr(m, "werkgever", None)
    )
    if werkgevers:
        naam, _ = werkgevers.most_common(1)[0]
        return _WERKGEVERS.get(_norm(naam)), naam

    suffixen = Counter()
    for medewerker in medewerkers:
        delen = str(medewerker.loonschaal or "").split()
        if len(delen) >= 2:
            suffixen[_norm(delen[-1])] += 1
    if suffixen:
        suffix, _ = suffixen.most_common(1)[0]
        return _SUFFIXEN.get(suffix), suffix
    return None, None


def bepaal_uzb(medewerkers, uzb_namen: dict[str, str]) -> str:
    """Leid het uitzendbureau af uit het bestand zelf.

    Scheelt een keuze in het scherm, en daarmee de mogelijkheid om de uren van
    het ene bureau tegen de tarieven van het andere af te rekenen.
    """
    werkgevers = {
        str(m.werkgever).strip() for m in medewerkers if getattr(m, "werkgever", None)
    }
    herkend = {_WERKGEVERS.get(_norm(w)) for w in werkgevers} - {None}
    # Level One regulier en jeugd-payroll staan in dezelfde export.
    if herkend <= {"L1", "L1_JEUGD"} and herkend:
        herkend = {"L1"}
    if len(herkend) > 1:
        namen = ", ".join(sorted(uzb_namen.get(h, h) for h in herkend))
        raise ValueError(
            f"dit bestand bevat meerdere uitzendbureaus ({namen}). "
            "Lever per bureau een aparte export aan."
        )
    if herkend:
        return herkend.pop()

    sleutel, ruw = herken_uzb(medewerkers)
    if sleutel is None:
        raise ValueError(
            "het uitzendbureau is niet af te leiden uit dit bestand. Zorg dat de "
            "kolom 'Werkgever op datum shift' is meegeëxporteerd, of dat de "
            "loonschalen zijn ingevuld."
        )
    return "L1" if sleutel == "L1_JEUGD" else sleutel


def controleer_uzb(medewerkers, verwacht: str, uzb_namen: dict[str, str]) -> None:
    """Weiger het bestand als het van een ander uitzendbureau blijkt te zijn."""
    gevonden, ruwe_naam = herken_uzb(medewerkers)
    if gevonden is None or gevonden in _FAMILIE.get(verwacht, {verwacht}):
        return
    raise ValueError(
        f"dit bestand hoort bij {uzb_namen.get(gevonden, gevonden)} "
        f"(gevonden: '{ruwe_naam}'), maar er is "
        f"{uzb_namen.get(verwacht, verwacht)} gekozen. Kies het juiste "
        "uitzendbureau of het juiste bestand."
    )
