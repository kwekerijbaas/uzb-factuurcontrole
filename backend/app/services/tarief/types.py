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
    # Ingangsdatum van de tariefperiode; alleen gevuld als er binnen de week
    # een nieuwe loontabel ingaat, zodat zichtbaar is waarom één categorie
    # twee regels met verschillende tarieven heeft.
    vanaf: date | None = None

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


@dataclass(frozen=True)
class Schaalreeks:
    """Het tarief van één kaartschaal in de tijd.

    Een CAO-loontabel gaat in op een vaste datum, niet op een weekgrens: per
    01-07-2026 en per 01-08-2026 valt die datum midden in een week. De uren van
    vóór en ná die dag horen dan tegen verschillende tarieven te lopen -- anders
    wordt een halve week tegen het verkeerde tarief afgerekend.

    `periodes` is oplopend op ingangsdatum; de eerste begint uiterlijk op de
    eerste dag van de week.
    """

    periodes: tuple[tuple[date, SchaalTarief | None], ...]

    @classmethod
    def constant(cls, schaal: SchaalTarief | None) -> "Schaalreeks":
        """Eén tarief voor de hele periode."""
        return cls(((date.min, schaal),))

    def index_op(self, dag: date) -> int:
        """Welke periode op `dag` geldt. Voor de eerste ingangsdatum geldt de
        eerste periode: die is er per definitie al vóór de week begon."""
        gekozen = 0
        for i, (vanaf, _) in enumerate(self.periodes):
            if vanaf <= dag:
                gekozen = i
        return gekozen

    def op(self, dag: date) -> SchaalTarief | None:
        return self.periodes[self.index_op(dag)][1] if self.periodes else None

    @property
    def is_constant(self) -> bool:
        return len(self.periodes) <= 1


@dataclass(frozen=True)
class Kaartreeks:
    """De tariefkaarten van één uitzendbureau in de tijd.

    De app kiest de kaart per dag in plaats van per week, zodat een loontabel
    die midden in de week ingaat vanaf díe dag geldt.
    """

    periodes: tuple[tuple[date, TariefKaart | None], ...] = ()

    @classmethod
    def van_kaart(cls, kaart: TariefKaart | None) -> "Kaartreeks":
        return cls(((date.min, kaart),))

    def op(self, dag: date) -> TariefKaart | None:
        if not self.periodes:
            return None
        gekozen = self.periodes[0][1]
        for vanaf, kaart in self.periodes:
            if vanaf <= dag:
                gekozen = kaart
        return gekozen

    def schalen_van(self, kaartcode: str | None) -> Schaalreeks:
        """De tariefreeks van één kaartschaal, om een medewerker af te rekenen."""
        return Schaalreeks(
            tuple(
                (vanaf, kaart.schaal(kaartcode) if kaart else None)
                for vanaf, kaart in self.periodes
            )
        )

    @property
    def kaarten(self) -> list[TariefKaart]:
        """De kaarten die in deze periode zijn gebruikt, op ingangsdatum."""
        return [kaart for _, kaart in self.periodes if kaart is not None]

    @property
    def is_leeg(self) -> bool:
        return all(kaart is None for _, kaart in self.periodes)
