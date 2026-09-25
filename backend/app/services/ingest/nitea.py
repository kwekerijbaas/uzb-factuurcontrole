"""Nitea 'Medewerker uren'-overzicht (.pdf) inlezen: werkelijke registratie.

Regelindeling per gewerkte dag:
    <Nr> <NiteaID> - <Naam> <DD-MM-YYYY> <begin> <einde> <werktijd> <pauze>
bv. `1 87 - Marius Mic 15-06-2026 6:59 16:02 7:45 1:15`

'Werk tijd' is de netto gewerkte tijd (pauze er al af); 'Pauze tijd' apart.
Bij een korte dienst staat er geen pauze; die kolom is dan leeg en de regel
eindigt na de werktijd. Kop-/voetregels (titels, perioderegel, paginanummers)
matchen het patroon niet en worden overgeslagen.

Nachtdiensten: een dienst over middernacht kan met een einddatum vóór de
eindtijd staan (`03-08-2026 22:57 04-08-2026 8:00 8:00 1:00`); die wordt
gelezen en de engine splitst hem op middernacht. Staat de eindtijd er niet
(regel met drie tijden waarvan begin-einde de werktijd bij lange na niet
verklaart), dan wordt de eindtijd uit begin + werktijd + pauze afgeleid in
plaats van een dienst van zestien uur met één gewerkt uur aan te nemen.

Regels die op een registratieregel lijken maar niet te lezen zijn, worden
in `overgeslagen` verzameld zodat de gebruiker ze te zien krijgt: een stil
weggelaten dag is een te laag weektotaal dat niemand opmerkt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from io import BytesIO
from pathlib import Path

import pdfplumber

from app.services.calc.types import RegistratieRegel

_REGEL = re.compile(
    r"^\s*\d+\s+"                       # volgnummer
    r"(?P<id>\d+)\s*-\s*"              # Nitea-ID
    r"(?P<naam>.+?)\s+"               # naam (non-greedy)
    r"(?P<datum>\d{2}-\d{2}-\d{4})\s+"
    r"(?P<begin>\d{1,2}:\d{2})\s+"
    # Nachtdienst: soms staat de einddatum vóór de eindtijd.
    r"(?:(?P<einddatum>\d{2}-\d{2}-\d{4})\s+)?"
    r"(?P<eind>\d{1,2}:\d{2})\s+"
    r"(?P<werk>\d{1,2}:\d{2})"
    # Zonder pauze eindigt de regel na de werktijd; die dienst telt gewoon mee.
    r"(?:\s+(?P<pauze>\d{1,2}:\d{2}))?\s*$"
)

# Lijkt op een registratieregel (volgnummer, Nitea-ID, streepje, een datum)
# maar is niet volgens `_REGEL` te lezen. Zulke regels worden gemeld.
_LIJKT_OP_REGEL = re.compile(r"^\s*\d+\s+\d+\s*-\s*\S.*\d{2}-\d{2}-\d{4}")

# Kop, naam en datum; de tijden erachter worden op kolompositie gelezen.
_KOPSTUK = re.compile(
    r"^\s*\d+\s+(?P<id>\d+)\s*-\s*(?P<naam>.+?)\s+"
    r"(?P<datum>\d{2}-\d{2}-\d{4})(?:\s+(?P<einddatum>\d{2}-\d{2}-\d{4}))?\s*(?P<rest>.*)$"
)
_TIJD = re.compile(r"^\d{1,2}:\d{2}$")

# De vier tijdkolommen van het overzicht 'Medewerker uren', zoals ze in de
# kopregel staan. Nitea laat cellen leeg bij nacht- en middagdiensten; welke
# tijd waar hoort is dan alleen aan de kolompositie te zien, niet aan de
# volgorde in de tekstregel.
_TIJDKOLOMMEN = (("begin", "Begin"), ("eind", "Einde"), ("werk", "Werk"), ("pauze", "Pauze"))

# Boven dit verschil tussen (einde - begin) en de werktijd is een regel met
# drie tijden eerder 'eindtijd ontbreekt' dan 'dienst met een lange
# onderbreking'. Vier uur: een echte split shift blijft daaronder.
_ONVERKLAARBAAR = 4 * 60


def _hm_naar_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _tijd(s: str) -> time:
    h, m = s.split(":")
    return time(int(h) % 24, int(m))


@dataclass
class NiteaMedewerker:
    naam: str
    nitea_id: str
    registratie: list[RegistratieRegel] = field(default_factory=list)


def _regel_uit(m: re.Match) -> tuple[RegistratieRegel, str | None]:
    """Maak van een gelezen regel een registratieregel.

    Geeft ook een opmerking terug als de regel anders gelezen is dan hij er
    staat (afgeleide eindtijd), zodat dat in het overzicht terechtkomt.
    """
    datum = datetime.strptime(m.group("datum"), "%d-%m-%Y").date()
    begin = _tijd(m.group("begin"))
    eind = _tijd(m.group("eind"))
    werk = _hm_naar_min(m.group("werk"))
    pauze = _hm_naar_min(m.group("pauze") or "0:00")
    opmerking = None

    if m.group("einddatum"):
        einddatum = datetime.strptime(m.group("einddatum"), "%d-%m-%Y").date()
        if einddatum != datum and einddatum != datum + timedelta(days=1):
            opmerking = (
                f"{datum:%d-%m}: einddatum {einddatum:%d-%m-%Y} ligt niet op de "
                "dag zelf of de dag erna; als dienst over middernacht gelezen"
            )

    elif m.group("pauze") is None:
        # Drie tijden: (begin, einde, werk) zonder pauze, óf (begin, werk,
        # pauze) zonder eindtijd. Verklaart begin-einde de werktijd bij lange
        # na niet, dan is het de tweede lezing.
        venster = (_naar_min(eind) - _naar_min(begin)) % (24 * 60) or 24 * 60
        if venster - werk > _ONVERKLAARBAAR:
            werk_alt, pauze_alt = _naar_min(eind), werk
            if 0 < werk_alt <= 16 * 60:
                eind_alt = (_naar_min(begin) + werk_alt + pauze_alt) % (24 * 60)
                opmerking = (
                    f"{datum:%d-%m}: geen eindtijd in Nitea; gelezen als "
                    f"{begin:%H:%M} + {werk_alt // 60}:{werk_alt % 60:02d} werk "
                    f"+ {pauze_alt // 60}:{pauze_alt % 60:02d} pauze = einde "
                    f"{eind_alt // 60:02d}:{eind_alt % 60:02d}"
                )
                eind, werk, pauze = time(eind_alt // 60, eind_alt % 60), werk_alt, pauze_alt

    return RegistratieRegel(datum, begin, eind, werk, pauze), opmerking


def _naar_min(t: time) -> int:
    return t.hour * 60 + t.minute


def _kolomposities(woorden: list[dict]) -> dict[str, float] | None:
    """Middenpositie van elke tijdkolom, uit de kopregel van de tabel."""
    posities: dict[str, float] = {}
    for sleutel, kop in _TIJDKOLOMMEN:
        treffers = [w for w in woorden if w["text"] == kop]
        if len(treffers) != 1:
            return None
        woord = treffers[0]
        # De kop staat als "Begin tijd"; het woord 'tijd' hoort erbij en
        # verschuift het midden van de kolom naar rechts.
        rechts = [
            w for w in woorden
            if w["text"].lower() == "tijd"
            and abs(w["top"] - woord["top"]) < 3
            and 0 < w["x0"] - woord["x1"] < 12
        ]
        einde = rechts[0]["x1"] if rechts else woord["x1"]
        posities[sleutel] = (woord["x0"] + einde) / 2
    return posities


def _tijden_op_kolom(
    woorden: list[dict], posities: dict[str, float]
) -> dict[str, str]:
    """Deel de tijden van één regel in op de kolom waar ze onder staan."""
    gevonden: dict[str, str] = {}
    for woord in woorden:
        if not _TIJD.match(woord["text"]):
            continue
        midden = (woord["x0"] + woord["x1"]) / 2
        sleutel = min(posities, key=lambda k: abs(posities[k] - midden))
        gevonden.setdefault(sleutel, woord["text"])
    return gevonden


def _regels_met_posities(pagina) -> list[tuple[str, dict[str, str]]]:
    """Per tekstregel de regeltekst met de tijden per kolom.

    Retourneert een lege lijst als de kopregel niet gevonden is; dan valt
    `lees_nitea` terug op lezen zonder posities.
    """
    woorden = pagina.extract_words()
    posities = _kolomposities(woorden)
    if posities is None:
        return []
    per_regel: dict[int, list[dict]] = {}
    for woord in woorden:
        per_regel.setdefault(round(woord["top"] / 3), []).append(woord)
    uit = []
    for _, groep in sorted(per_regel.items()):
        groep.sort(key=lambda w: w["x0"])
        tekst = " ".join(w["text"] for w in groep)
        uit.append((tekst, _tijden_op_kolom(groep, posities)))
    return uit


def _uit_kolommen(
    m: re.Match, kolommen: dict[str, str]
) -> tuple[RegistratieRegel, str | None] | None:
    """Bouw een registratieregel uit de op kolom gelezen tijden.

    Ontbreekt de werktijd, dan valt er niets te tellen en wordt de regel
    gemeld. Ontbreekt alleen begin of eind, dan wordt die uit de andere plus
    werktijd en pauze afgeleid.
    """
    if "werk" not in kolommen:
        return None
    datum = datetime.strptime(m.group("datum"), "%d-%m-%Y").date()
    werk = _hm_naar_min(kolommen["werk"])
    pauze = _hm_naar_min(kolommen.get("pauze", "0:00"))
    begin = _tijd(kolommen["begin"]) if "begin" in kolommen else None
    eind = _tijd(kolommen["eind"]) if "eind" in kolommen else None
    opmerking = None

    if begin is not None and eind is None:
        einde_min = (_naar_min(begin) + werk + pauze) % (24 * 60)
        eind = time(einde_min // 60, einde_min % 60)
        opmerking = (
            f"{datum:%d-%m}: geen eindtijd in Nitea; gelezen als "
            f"{begin:%H:%M} + werktijd + pauze = einde {eind:%H:%M}"
        )
    elif eind is not None and begin is None:
        begin_min = (_naar_min(eind) - werk - pauze) % (24 * 60)
        begin = time(begin_min // 60, begin_min % 60)
        opmerking = (
            f"{datum:%d-%m}: geen begintijd in Nitea; gelezen als einde "
            f"{eind:%H:%M} min werktijd en pauze = begin {begin:%H:%M}"
        )
    elif begin is None and eind is None:
        opmerking = (
            f"{datum:%d-%m}: geen begin- en eindtijd in Nitea. De "
            f"{kolommen['werk']} werktijd telt mee, maar zonder tijden is geen "
            "nacht-, avond- of weekendtoeslag vast te stellen."
        )

    return RegistratieRegel(datum, begin, eind, werk, pauze), opmerking


def lees_nitea(
    bron: str | Path | bytes, overgeslagen: list[str] | None = None
) -> list[NiteaMedewerker]:
    """Parse een Nitea-PDF naar één NiteaMedewerker per medewerker.

    `overgeslagen` (optioneel) wordt gevuld met regels die op een
    registratieregel lijken maar niet te lezen waren, en met opmerkingen over
    regels die anders gelezen zijn dan ze er staan.
    """
    data = BytesIO(bron) if isinstance(bron, (bytes, bytearray)) else bron
    per_id: dict[str, NiteaMedewerker] = {}

    with pdfplumber.open(data) as pdf:
        for pagina in pdf.pages:
            # Eerst op kolompositie: alleen zo is te zien of een losse tijd de
            # begin-, eind-, werk- of pauzetijd is. Nitea laat cellen leeg bij
            # nacht- en middagdiensten, en dan klopt de volgorde niet meer.
            regels = _regels_met_posities(pagina)
            if not regels:
                regels = [(r, {}) for r in (pagina.extract_text() or "").split("\n")]

            for regel, kolommen in regels:
                m = _KOPSTUK.match(regel) if kolommen else _REGEL.match(regel)
                if not m:
                    if overgeslagen is not None and _LIJKT_OP_REGEL.match(regel):
                        overgeslagen.append(re.sub(r"\s+", " ", regel).strip())
                    continue
                nid = m.group("id")
                naam = re.sub(r"\s+", " ", m.group("naam")).strip()

                if kolommen:
                    uitkomst = _uit_kolommen(m, kolommen)
                    if uitkomst is None:
                        if overgeslagen is not None:
                            overgeslagen.append(re.sub(r"\s+", " ", regel).strip())
                        continue
                    registratie, opmerking = uitkomst
                else:
                    registratie, opmerking = _regel_uit(m)
                if opmerking and overgeslagen is not None:
                    overgeslagen.append(f"{naam} {opmerking}")

                mw = per_id.setdefault(nid, NiteaMedewerker(naam=naam, nitea_id=nid))
                mw.registratie.append(registratie)

    return sorted(per_id.values(), key=lambda m: m.naam)
