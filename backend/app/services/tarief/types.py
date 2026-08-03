"""Domeintypes voor de tariefmapping per UZB (SPEC §5).

Losgekoppeld van de database zodat de bedragberekening als pure functie
testbaar is, net als de calc-engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

# Tariefcategorieën zoals ze op de UZB-tariefkaarten staan.
CAT_100 = "100"  # basistarief (bij L1/SW ook het 135%-overwerktarief)
CAT_135 = "135"  # apart overwerktarief (alleen Cervokordaat)
CAT_150 = "150"
CAT_200 = "200"
CAT_FEESTDAG = "feestdag"
CAT_NACHTUUR = "nachtuur"  # Sterk Werk: 'Totaal nachtuur'
CAT_NACHT_50 = "nacht50"  # Sterk Werk: losse '50% nacht'-opslag
CAT_115 = "115"  # Cervokordaat: middagtoeslag
CAT_122 = "122"  # Cervokordaat: avondtoeslag

ALLE_CATEGORIEEN = (
    CAT_100,
    CAT_135,
    CAT_115,
    CAT_122,
    CAT_150,
    CAT_200,
    CAT_FEESTDAG,
    CAT_NACHTUUR,
    CAT_NACHT_50,
)


@dataclass(frozen=True)
class SchaalTarief:
    """Alle tarieven van één loonschaal, per categorie."""

    code: str  # genormaliseerde kaartcode, bv. "B2F" (L1) of "B2" (SW)
    tarieven: dict[str, Decimal]

    def tarief(self, categorie: str) -> Decimal | None:
        return self.tarieven.get(categorie)


@dataclass(frozen=True)
class UzbConventies:
    """Hoe één UZB de toeslagen factureert (SPEC §5).

    `bron_naar_categorie` vertaalt de toeslag-bron uit de calc-trace naar de
    tariefkolom op de kaart. Dit is nodig omdat één percentage meerdere
    tarieven kan hebben: nacht/avond/zaterdag/feestdag zijn alle 50%, maar
    Sterk Werk kent een apart nachtuur-tarief en Level One een apart
    feestdag-tarief.
    """

    sleutel: str  # bv. "L1", "SW", "CK"
    naam: str
    bron_naar_categorie: dict[str, str]
    # regex -> vervanging, toegepast op de SNOOP-code om de kaartcode te maken
    code_regels: tuple[tuple[str, str], ...] = ()
    # dag-grens (>10u/dag = 50%) wel/niet doorbelasten
    dag_grens_factureren: bool = True

    def kaartcode(self, snoop_code: str | None) -> str | None:
        """Zet een SNOOP-loonschaal om naar de code op de tariefkaart."""
        if not snoop_code:
            return None
        code = re.sub(r"\s+", " ", str(snoop_code)).strip()
        for patroon, vervanging in self.code_regels:
            nieuw, aantal = re.subn(patroon, vervanging, code, flags=re.IGNORECASE)
            if aantal:
                return nieuw.strip()
        return code


@dataclass
class TariefKaart:
    """Eén versie van de tariefkaart van één UZB (SPEC §6).

    `geldig_van`/`geldig_tot` spiegelen het SCD2-patroon in de database, zodat
    CAO- en minimumloonwijzigingen automatisch meebewegen: bij het verwerken
    van een week wordt de kaart gekozen die op die weekdatums geldig was.
    """

    uzb_sleutel: str
    geldig_van: date
    geldig_tot: date | None = None
    schalen: dict[str, SchaalTarief] = field(default_factory=dict)

    def geldig_op(self, dag: date) -> bool:
        if dag < self.geldig_van:
            return False
        return self.geldig_tot is None or dag <= self.geldig_tot

    def schaal(self, kaartcode: str | None) -> SchaalTarief | None:
        return self.schalen.get(kaartcode) if kaartcode else None


def kies_kaart(kaarten: list[TariefKaart], dag: date) -> TariefKaart | None:
    """De tariefkaart die op `dag` geldig was; bij overlap de meest recente."""
    geldig = [k for k in kaarten if k.geldig_op(dag)]
    return max(geldig, key=lambda k: k.geldig_van) if geldig else None


@dataclass
class BedragRegel:
    """Eén tariefregel van de berekening — de onderbouwing van het bedrag."""

    categorie: str
    bronnen: tuple[str, ...]
    minuten: int
    tarief: Decimal
    bedrag: Decimal

    @property
    def uren(self) -> Decimal:
        return (Decimal(self.minuten) / Decimal(60)).quantize(Decimal("0.01"))


@dataclass
class BedragResultaat:
    regels: list[BedragRegel] = field(default_factory=list)
    ontbrekende_tarieven: list[str] = field(default_factory=list)

    @property
    def totaal(self) -> Decimal:
        return sum((r.bedrag for r in self.regels), Decimal("0")).quantize(Decimal("0.01"))

    @property
    def minuten(self) -> int:
        return sum(r.minuten for r in self.regels)
