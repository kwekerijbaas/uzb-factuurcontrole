"""Namen van uitzendkrachten koppelen tussen bronnen.

Dezelfde persoon staat in Nitea, SNOOP, de uitzendkrachtenlijst en op de
factuur niet altijd hetzelfde geschreven:

    Nitea                      elders
    Cristian Bogdan Demian     Christian Bogdan Demian   (spelling)
    Visile Andrei Tiron        Vasile Andrei Tiron       (typefout)
    Robert Ionut Grasu         Ionut Robert Grasu        (volgorde)
    Elena Grasu                Raluca Elena Grasu        (naamdeel weggelaten)
    Isabela Doicsar            Isabela Victoria Doicsar  (naamdeel weggelaten)
    K.P. Sliwa (Kamil)         Kamil Sliwa               (factuur: initialen)

Zonder koppeling krijgt zo iemand geen loonschaal en dus geen tarief, terwijl
SNOOP die schaal wel heeft -- zijn uren staan dan zonder bedrag in het
overzicht en het weektotaal is te laag.

Er wordt op **achternaam** gekoppeld, met de voornaam of initiaal als
scheidsrechter bij naamgenoten (bij Sterk Werk werken drie mensen Grasu). Een
koppeling op gelijkenis wordt altijd gemeld, zodat een verkeerde koppeling
zichtbaar is in plaats van stilzwijgend een verkeerd tarief op te leveren.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

# Minimale gelijkenis van de achternaam voordat twee namen dezelfde persoon
# kunnen zijn. 85 laat "Demian"/"Demian" en "Tiron"/"Tiron" door, maar niet
# "Grasu"/"Gruca".
_ACHTERNAAM_DREMPEL = 85


def _zonder_accenten(tekst: str) -> str:
    """"Śliwa" -> "sliwa", "Gheorghiţă" -> "gheorghita".

    Poolse en Roemeense namen staan niet overal met dezelfde accenten. Voor
    `fuzz.ratio` telt zo'n letter als een volledige wijziging, waardoor
    "śliwa"/"sliwa" op 80 uitkwam en de koppeling afketste.
    """
    ontleed = unicodedata.normalize("NFKD", str(tekst or ""))
    # De Poolse ł valt niet uiteen in NFKD en wordt apart vervangen.
    ontleed = ontleed.replace("\u0142", "l").replace("\u0141", "L")
    return "".join(teken for teken in ontleed if not unicodedata.combining(teken))


def delen(naam: str) -> tuple[str, set[str]]:
    """Splits een naam in achternaam en de overige naamdelen (kleine letters).

    "K.P. Sliwa (Kamil)" -> ("sliwa", {"k", "p", "kamil"})
    "Adelina Iuliana Boca" -> ("boca", {"adelina", "iuliana"})
    """
    tekst = re.sub(r"\s+", " ", _zonder_accenten(naam)).strip()
    haakjes = re.findall(r"\(([^)]*)\)", tekst)
    tekst = re.sub(r"\([^)]*\)", " ", tekst).strip()
    woorden = [w for w in re.split(r"\s+", tekst) if w]
    if not woorden:
        return "", set()
    achternaam = woorden[-1].lower()
    overig = {
        deel.lower().strip(".")
        for woord in woorden[:-1]
        for deel in woord.split(".")
        if deel.strip(".")
    }
    overig |= {h.lower() for h in haakjes if h.strip()}
    return achternaam, overig


def past(links: str, rechts: str) -> int:
    """Score voor het koppelen van twee namen; 0 betekent geen match."""
    l_achter, l_overig = delen(links)
    r_achter, r_overig = delen(rechts)
    if not l_achter or not r_achter:
        return 0

    gelijkenis = fuzz.ratio(l_achter, r_achter)
    if gelijkenis < _ACHTERNAAM_DREMPEL:
        return 0

    score = int(gelijkenis)
    # voornaam of initiaal erbij laat naamgenoten uit elkaar houden
    if l_overig & r_overig:
        score += 40
    elif any(
        voor[0] == initiaal
        for voor in l_overig
        for initiaal in r_overig
        if len(initiaal) == 1 and voor
    ):
        score += 20
    return score


# Een gelijke achternaam alleen is niet genoeg om een loonschaal over te
# nemen: "Jan Bakker" en "Piet Bakker" zijn twee mensen. Er moet ook een
# voornaam of initiaal overeenkomen, en dat is precies wat `past` boven de 100
# uit tilt (achternaam maximaal 100, plus 20 voor een initiaal of 40 voor een
# voornaam).
MINIMUM_ZEKERHEID = 105


def beste_match(naam: str, kandidaten, minimum: int = MINIMUM_ZEKERHEID) -> str | None:
    """De kandidaat die zeker dezelfde persoon is, of niets.

    Zeker betekent twee dingen: de score haalt `minimum` (dus niet alleen de
    achternaam komt overeen), en er is één duidelijke winnaar. Delen twee
    kandidaten de hoogste score (twee broers met dezelfde initiaal), dan wordt
    er niet gekoppeld -- een gok levert een verkeerd tarief op zonder dat
    iemand het ziet.
    """
    scores = sorted(
        ((past(naam, kandidaat), kandidaat) for kandidaat in kandidaten),
        key=lambda p: (-p[0], p[1]),
    )
    if not scores or scores[0][0] < minimum:
        return None
    if len(scores) > 1 and scores[1][0] == scores[0][0]:
        return None
    return scores[0][1]
