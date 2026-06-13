"""Domeintypes voor de CAO-calculatie. Bewust losgekoppeld van de database
zodat de engine als pure functies te testen is."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal

MIN_PER_DAG = 1440
MIN_PER_WEEK = MIN_PER_DAG * 7


@dataclass(frozen=True)
class ToeslagRegel:
    """Eén CAO-toeslagregel. Spiegelt de bewerkbare DB-tabel `toeslag_regel`
    (SCD2). De engine leest deze regels; ze staan niet hard in code."""

    code: str
    percentage: Decimal
    weekdagen: frozenset[int] = frozenset()  # ISO 1=ma .. 7=zo; leeg = elke dag
    minuut_van: int = 0  # minuut-van-de-dag 0..1440, inclusief
    minuut_tot: int = MIN_PER_DAG  # exclusief
    alleen_feestdag: bool = False
    prioriteit: int = 0

    def matcht(self, iso_weekdag: int, minuut: int, is_feestdag: bool) -> bool:
        if self.weekdagen and iso_weekdag not in self.weekdagen:
            return False
        if not (self.minuut_van <= minuut < self.minuut_tot):
            return False
        if self.alleen_feestdag and not is_feestdag:
            return False
        return True


@dataclass(frozen=True)
class RegistratieRegel:
    """Werkelijke registratie uit Nitea. `gewerkte_minuten` is netto (pauze al
    afgetrokken door Nitea); de engine trekt zelf géén pauze af."""

    datum: date
    begin: time
    eind: time
    gewerkte_minuten: int
    pauze_minuten: int = 0


@dataclass(frozen=True)
class PlanningRegel:
    """Geplande inzet uit SNOOP."""

    datum: date
    begin: time
    eind: time
    geplande_minuten: int


@dataclass
class Afwijking:
    datum: date
    soort: str  # zie SOORT_* hieronder
    detail: str
    planning_minuten: int | None = None
    registratie_minuten: int | None = None


SOORT_UREN_VERSCHIL = "uren_verschil"
SOORT_TIJD_VERSCHIL = "tijd_verschil"
SOORT_REGISTRATIE_INCONSISTENT = "registratie_inconsistent"
SOORT_GEEN_PLANNING = "geen_planning"
SOORT_GEEN_REGISTRATIE = "geen_registratie"


@dataclass
class TraceSegment:
    """Audit-uitleg: waarom kreeg dit blok deze toeslag."""

    datum: date
    minuut_van: int
    minuut_tot: int
    percentage: Decimal
    bron: str  # bv. "avond", "overwerk_35", "dag_grens_50", "week_grens_50", "normaal"


@dataclass
class WeekParameters:
    """Per uzk × week instelbare parameters."""

    weeknorm_minuten: int = 38 * 60  # jaarurenmodel-norm, default 38u
    dag_grens_minuten: int = 10 * 60  # > 10u/dag => 50%
    week_grens_minuten: int = 48 * 60  # > 48u/week => 50%
    overwerk_percentage: Decimal = Decimal("35")
    dag_grens_percentage: Decimal = Decimal("50")
    week_grens_percentage: Decimal = Decimal("50")
    week_50u_uitzondering: bool = False  # art. 18 lid 4d: 8 weken/jaar geen 50% >48u


@dataclass
class WeekResultaat:
    netto_minuten: int
    minuten_per_percentage: dict[Decimal, int]
    afwijkingen: list[Afwijking] = field(default_factory=list)
    trace: list[TraceSegment] = field(default_factory=list)

    def uren_per_percentage(self) -> dict[Decimal, Decimal]:
        return {
            pct: (Decimal(m) / Decimal(60)).quantize(Decimal("0.01"))
            for pct, m in sorted(self.minuten_per_percentage.items())
        }

    @property
    def netto_uren(self) -> Decimal:
        return (Decimal(self.netto_minuten) / Decimal(60)).quantize(Decimal("0.01"))
