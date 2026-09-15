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

# genormaliseerde werkgeversnaam -> UZB-sleutel. SNOOP noteert varianten die
# elkaar overlappen ("Level One", "Level One Payroll", "Level One Payroll
# Jeugd"), dus wint de langste die past -- anders zou het jeugd-payroll als
# regulier Level One worden gelezen.
_WERKGEVERS = {
    "levelone": "L1",
    "levelonejeugd": "L1_JEUGD",
    "levelonepayroll": "L1_JEUGD",
    "levelonepayrolljeugd": "L1_JEUGD",
    "sterkwerk": "SW",
    # SNOOP schrijft Cervokordaat ook kortweg als "Kordaat". De schalen staan
    # daar zonder achtervoegsel ("B2", "C4") en passen op de CK-kaart.
    "cervokordaat": "CK",
    "kordaat": "CK",
}

# Eigen organisatie: geen uitzendbureau. Deze regels horen niet op de
# uitzendkrachtenlijst en worden overgeslagen in plaats van geweigerd.
_EIGEN = {"kwekerijbaas"}

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


def familie_van(sleutel: str) -> set[str]:
    """De bureaus die met dit bureau bestanden delen (inclusief zichzelf)."""
    return set(_FAMILIE.get(sleutel, {sleutel}))


def _norm(waarde: str | None) -> str:
    return re.sub(r"[^a-z]", "", str(waarde or "").lower())


def _sleutel_van_werkgever(naam: str | None) -> str | None:
    """Zoek de UZB-sleutel bij een werkgeversnaam uit SNOOP.

    Op de langste passende naam, zodat een toevoeging aan de naam ("Level One"
    -> "Level One Payroll Jeugd") het bestand niet onherkenbaar maakt.
    """
    genormaliseerd = _norm(naam)
    if not genormaliseerd:
        return None
    for kandidaat in sorted(_WERKGEVERS, key=len, reverse=True):
        if genormaliseerd.startswith(kandidaat):
            return _WERKGEVERS[kandidaat]
    return None


def herken_uzb(medewerkers) -> tuple[str | None, str | None]:
    """Bepaal uit welke bron dit bestand komt.

    Retourneert (sleutel, gevonden naam). De werkgeverskolom telt het zwaarst;
    zonder die kolom wordt op het achtervoegsel van de loonschaal teruggevallen.
    """
    werkgevers = Counter(
        str(m.werkgever).strip() for m in medewerkers if getattr(m, "werkgever", None)
    )
    for naam, _ in werkgevers.most_common():
        sleutel = _sleutel_van_werkgever(naam)
        if sleutel is not None:
            return sleutel, naam

    # Onbekende werkgeversnaam: het achtervoegsel van de loonschaal verraadt het
    # bureau ook. Zonder deze terugval zou één hernoemd bureau het bestand
    # onbruikbaar maken.
    suffixen = Counter()
    for medewerker in medewerkers:
        delen = str(medewerker.loonschaal or "").split()
        if len(delen) >= 2:
            suffixen[_norm(delen[-1])] += 1
    if suffixen:
        suffix, _ = suffixen.most_common(1)[0]
        return _SUFFIXEN.get(suffix), suffix
    return None, None


def verdeel_per_uzb(regels, uzb_namen: dict[str, str]) -> tuple[dict[str, list], list[dict]]:
    """Verdeel de regels van één export over de uitzendbureaus.

    Een SNOOP-lijst over een langere periode bevat alle bureaus door elkaar;
    per regel staat het bureau erbij. Retourneert de verdeling én de regels
    die niet te plaatsen zijn, elk met de reden.

    Die tweede lijst wordt overgeslagen in plaats van het hele bestand te
    weigeren: een jaarlijst bevat naast de uitzendbureaus ook eigen
    medewerkers en bureaus die niet in de app zijn ingericht (geen
    tariefkaart). Eén zo'n naam mag de overige driehonderd niet tegenhouden --
    maar ze moeten wel gemeld worden, want voor hen komt er geen tarief uit
    SNOOP.
    """
    per_uzb: dict[str, list] = {}
    niet_geplaatst: list[dict] = []
    for regel in regels:
        werkgever = str(getattr(regel, "werkgever", None) or "").strip()
        sleutel = _sleutel_van_werkgever(werkgever)
        if sleutel is None:
            sleutel, _ = herken_uzb([regel])
        if sleutel is not None and sleutel in uzb_namen:
            per_uzb.setdefault(sleutel, []).append(regel)
            continue
        niet_geplaatst.append(
            {
                "naam": regel.naam,
                "werkgever": werkgever or None,
                "loonschaal": getattr(regel, "loonschaal", None),
                "reden": _reden(werkgever),
            }
        )
    if not per_uzb and niet_geplaatst:
        raise ValueError(
            "bij geen enkele regel is het uitzendbureau te bepalen. Zorg dat de "
            "kolom 'Werkgever op datum shift' is gevuld."
        )
    return per_uzb, niet_geplaatst


def _reden(werkgever: str) -> str:
    """Waarom deze regel niet te plaatsen is, in de taal van de gebruiker."""
    if not werkgever:
        return (
            "geen werkgever in het bestand en de loonschaal verraadt het bureau "
            "niet (kolom 'Werkgever op datum shift' leeg)"
        )
    if _norm(werkgever) in _EIGEN:
        return f"'{werkgever}' is geen uitzendbureau maar de eigen organisatie"
    return (
        f"uitzendbureau '{werkgever}' is niet in de app ingericht; daar is geen "
        "tariefkaart voor, dus ook geen tarief"
    )


def bepaal_uzb(medewerkers, uzb_namen: dict[str, str]) -> str:
    """Leid het uitzendbureau af uit het bestand zelf.

    Scheelt een keuze in het scherm, en daarmee de mogelijkheid om de uren van
    het ene bureau tegen de tarieven van het andere af te rekenen.
    """
    werkgevers = {
        str(m.werkgever).strip() for m in medewerkers if getattr(m, "werkgever", None)
    }
    herkend = {_sleutel_van_werkgever(w) for w in werkgevers} - {None}
    # Staan regulier en jeugd-payroll in één export, dan is het een Level
    # One-bestand. Een export met alleen jeugd-payroll blijft L1_JEUGD: dat
    # heeft een eigen tariefkaart, en tegen de reguliere tarieven afrekenen zou
    # de jeugduren fors te hoog waarderen.
    if herkend == {"L1", "L1_JEUGD"}:
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
    return sleutel


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
