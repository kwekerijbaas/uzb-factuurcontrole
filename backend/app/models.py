"""SQLAlchemy-modellen voor de uitzenduren-controle app.

Ontwerpkeuzes:
- SCD2 (valid_from/valid_to) op `loonschaal` en `toeslag_regel`: een controle
  achteraf gebruikt altijd de regels die golden in die week.
- `planning_run` en `registratie_run` zijn immutable; correcties = nieuwe run
  met `vorige_run_id` naar de voorganger.
- `match_periode` (uzk × week) is de enige schrijfbare werkeenheid voor de
  planner; `audit_log` hangt daaraan.
- Geen projectcode/VRC: de app dient puur voor uren-vs-factuur-controle.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    ARRAY,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TijdstempelMixin:
    aangemaakt_op: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    gewijzigd_op: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Scd2Mixin:
    """Slowly Changing Dimension type 2: periode-geldigheid."""

    geldig_van: Mapped[date] = mapped_column(Date, nullable=False)
    geldig_tot: Mapped[date | None] = mapped_column(Date, nullable=True)  # NULL = actueel


class Uzb(Base, TijdstempelMixin):
    __tablename__ = "uzb"

    id: Mapped[uuid.UUID] = _pk()
    naam: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    contact_email: Mapped[str | None] = mapped_column(String(320))
    factuur_prefix: Mapped[str | None] = mapped_column(String(50))
    # afronding: {"strategie": "nearest|up|down", "minuten": 15} -- bron Nitea indien leeg
    afronding: Mapped[dict | None] = mapped_column(JSONB)
    actief: Mapped[bool] = mapped_column(Boolean, default=True)

    uzks: Mapped[list[Uzk]] = relationship(back_populates="uzb")


class Loonschaal(Base, Scd2Mixin, TijdstempelMixin):
    __tablename__ = "loonschaal"

    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    omschrijving: Mapped[str | None] = mapped_column(String(200))
    uurtarief: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)


class CaoLoontabel(Base, TijdstempelMixin):
    """Eén geüploade CAO-loontabel. Vanaf `ingangsdatum` gelden deze lonen; de
    UZB-tarieven bewegen automatisch mee (zie UzbTariefFactor)."""

    __tablename__ = "cao_loontabel"

    id: Mapped[uuid.UUID] = _pk()
    naam: Mapped[str] = mapped_column(String(200), nullable=False)
    ingangsdatum: Mapped[date] = mapped_column(Date, nullable=False)
    bron_bestand: Mapped[str | None] = mapped_column(String(500))
    geimporteerd_door: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    lonen: Mapped[list[CaoLoon]] = relationship(
        back_populates="loontabel", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("ingangsdatum", name="uq_cao_loontabel_ingangsdatum"),)


class CaoLoon(Base):
    """Uurloon van één CAO-schaal binnen een loontabel."""

    __tablename__ = "cao_loon"

    id: Mapped[uuid.UUID] = _pk()
    loontabel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cao_loontabel.id", ondelete="CASCADE"), nullable=False
    )
    schaal_code: Mapped[str] = mapped_column(String(50), nullable=False)  # bv. "B2", "15B2"
    omschrijving: Mapped[str | None] = mapped_column(String(200))
    uurloon: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)

    loontabel: Mapped[CaoLoontabel] = relationship(back_populates="lonen")

    __table_args__ = (
        UniqueConstraint("loontabel_id", "schaal_code", name="uq_cao_loon_tabel_schaal"),
    )


class UzbTariefFactor(Base, Scd2Mixin, TijdstempelMixin):
    """Omrekenfactor per UZB, per tariefkaart-schaal, per tariefcategorie:

        tarief = CAO-uurloon x factor

    De factor ligt contractueel vast met het uitzendbureau en verandert niet mee
    met de CAO-lonen. Daardoor levert het uploaden van een nieuwe loontabel
    automatisch een nieuwe, correcte tariefkaart op vanaf die ingangsdatum
    (SPEC §6).

    `kaartcode` is de code van het uitzendbureau (bv. "B4F" of "B4V"), die naar
    dezelfde `cao_schaal_code` ("B4") kan verwijzen met een andere factor --
    Flex en Vast delen immers het CAO-loon maar niet het tarief.
    """

    __tablename__ = "uzb_tarief_factor"

    id: Mapped[uuid.UUID] = _pk()
    uzb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("uzb.id"), nullable=False)
    kaartcode: Mapped[str] = mapped_column(String(50), nullable=False)
    cao_schaal_code: Mapped[str] = mapped_column(String(50), nullable=False)
    categorie: Mapped[str] = mapped_column(String(20), nullable=False)
    # 100 | 135 | 150 | 200 | feestdag | nachtuur
    factor: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "uzb_id", "kaartcode", "categorie", "geldig_van",
            name="uq_uzb_tarief_factor_versie",
        ),
    )


class Uzk(Base, TijdstempelMixin):
    __tablename__ = "uzk"

    id: Mapped[uuid.UUID] = _pk()
    uzb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("uzb.id"), nullable=False)
    externe_code: Mapped[str | None] = mapped_column(String(100))  # id in Nitea
    naam: Mapped[str] = mapped_column(String(200), nullable=False)
    loonschaal_code: Mapped[str | None] = mapped_column(String(50))
    actief: Mapped[bool] = mapped_column(Boolean, default=True)

    uzb: Mapped[Uzb] = relationship(back_populates="uzks")


class ToeslagRegel(Base, Scd2Mixin, TijdstempelMixin):
    """Bewerkbaar door admin. De engine leest deze regels (geen hardcoded %)."""

    __tablename__ = "toeslag_regel"

    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    omschrijving: Mapped[str | None] = mapped_column(String(200))
    percentage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    weekdagen: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))  # ISO 1..7, NULL=alle
    minuut_van: Mapped[int] = mapped_column(Integer, default=0)
    minuut_tot: Mapped[int] = mapped_column(Integer, default=1440)
    alleen_feestdag: Mapped[bool] = mapped_column(Boolean, default=False)
    voorwaarde: Mapped[dict | None] = mapped_column(JSONB)
    prioriteit: Mapped[int] = mapped_column(Integer, default=0)


class CaoKalender(Base, TijdstempelMixin):
    __tablename__ = "cao_kalender"

    id: Mapped[uuid.UUID] = _pk()
    datum: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    is_feestdag: Mapped[bool] = mapped_column(Boolean, default=False)
    omschrijving: Mapped[str | None] = mapped_column(String(200))


class Weekrooster(Base, TijdstempelMixin):
    __tablename__ = "weekrooster"

    id: Mapped[uuid.UUID] = _pk()
    uzk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("uzk.id"), nullable=False)
    iso_jaar: Mapped[int] = mapped_column(Integer, nullable=False)
    iso_week: Mapped[int] = mapped_column(Integer, nullable=False)
    norm_minuten: Mapped[int] = mapped_column(Integer, default=38 * 60)
    week_50u_uitzondering: Mapped[bool] = mapped_column(Boolean, default=False)


class PlanningRun(Base, TijdstempelMixin):
    __tablename__ = "planning_run"

    id: Mapped[uuid.UUID] = _pk()
    bron_bestand: Mapped[str | None] = mapped_column(String(500))
    vorige_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("planning_run.id"))
    geimporteerd_door: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    regels: Mapped[list[PlanningRegel]] = relationship(back_populates="run")


class PlanningRegel(Base):
    __tablename__ = "planning_regel"

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("planning_run.id"), nullable=False)
    uzk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("uzk.id"), nullable=False)
    datum: Mapped[date] = mapped_column(Date, nullable=False)
    begin: Mapped[time] = mapped_column(Time, nullable=False)
    eind: Mapped[time] = mapped_column(Time, nullable=False)
    geplande_minuten: Mapped[int] = mapped_column(Integer, nullable=False)

    run: Mapped[PlanningRun] = relationship(back_populates="regels")


class RegistratieRun(Base, TijdstempelMixin):
    __tablename__ = "registratie_run"

    id: Mapped[uuid.UUID] = _pk()
    uzb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("uzb.id"), nullable=False)  # verplicht
    bron_bestand: Mapped[str | None] = mapped_column(String(500))
    vorige_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("registratie_run.id"))
    geimporteerd_door: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    regels: Mapped[list[RegistratieRegel]] = relationship(back_populates="run")


class RegistratieRegel(Base):
    __tablename__ = "registratie_regel"

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("registratie_run.id"), nullable=False)
    uzk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("uzk.id"), nullable=False)
    datum: Mapped[date] = mapped_column(Date, nullable=False)
    begin: Mapped[time] = mapped_column(Time, nullable=False)
    eind: Mapped[time] = mapped_column(Time, nullable=False)
    gewerkte_minuten: Mapped[int] = mapped_column(Integer, nullable=False)  # netto, uit Nitea
    pauze_minuten: Mapped[int] = mapped_column(Integer, default=0)

    run: Mapped[RegistratieRun] = relationship(back_populates="regels")


class MatchPeriode(Base, TijdstempelMixin):
    """Eén weekcontrole (uzk × week). Enige schrijfbare werkeenheid."""

    __tablename__ = "match_periode"

    id: Mapped[uuid.UUID] = _pk()
    uzk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("uzk.id"), nullable=False)
    iso_jaar: Mapped[int] = mapped_column(Integer, nullable=False)
    iso_week: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="open")
    # open | gevalideerd | verzonden | gefactureerd | afwijking
    afwijkingen: Mapped[list | None] = mapped_column(JSONB)
    gevalideerd_door: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    gevalideerd_op: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    berekening: Mapped[BerekendeUren | None] = relationship(
        back_populates="match", uselist=False
    )


class BerekendeUren(Base, TijdstempelMixin):
    __tablename__ = "berekende_uren"

    id: Mapped[uuid.UUID] = _pk()
    match_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("match_periode.id"), nullable=False)
    netto_minuten: Mapped[int] = mapped_column(Integer, nullable=False)
    # {"0": 2280, "35": 240, "50": 120, "100": 0}
    minuten_per_percentage: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trace: Mapped[list | None] = mapped_column(JSONB)

    match: Mapped[MatchPeriode] = relationship(back_populates="berekening")


class Verzending(Base, TijdstempelMixin):
    __tablename__ = "verzending"

    id: Mapped[uuid.UUID] = _pk()
    uzb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("uzb.id"), nullable=False)
    iso_jaar: Mapped[int] = mapped_column(Integer, nullable=False)
    iso_week: Mapped[int] = mapped_column(Integer, nullable=False)
    bestand_pad: Mapped[str | None] = mapped_column(String(500))
    verzonden_op: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verzonden_door: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Factuur(Base, TijdstempelMixin):
    __tablename__ = "factuur"

    id: Mapped[uuid.UUID] = _pk()
    uzb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("uzb.id"), nullable=False)
    factuurnummer: Mapped[str | None] = mapped_column(String(100))
    bron_bestand: Mapped[str | None] = mapped_column(String(500))
    totaal_bedrag: Mapped[float | None] = mapped_column(Numeric(12, 2))

    regels: Mapped[list[FactuurRegel]] = relationship(back_populates="factuur")


class FactuurRegel(Base):
    __tablename__ = "factuur_regel"

    id: Mapped[uuid.UUID] = _pk()
    factuur_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("factuur.id"), nullable=False)
    omschrijving: Mapped[str | None] = mapped_column(String(500))
    uzk_naam_ruw: Mapped[str | None] = mapped_column(String(200))  # ruwe tekst voor fuzzy match
    match_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("match_periode.id"))
    match_score: Mapped[float | None] = mapped_column(Numeric(5, 2))  # fuzzy score 0-100
    uren: Mapped[float | None] = mapped_column(Numeric(8, 2))
    bedrag: Mapped[float | None] = mapped_column(Numeric(12, 2))

    factuur: Mapped[Factuur] = relationship(back_populates="regels")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = _pk()
    tijdstip: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actor: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    entiteit: Mapped[str] = mapped_column(String(100), nullable=False)
    entiteit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actie: Mapped[str] = mapped_column(String(50), nullable=False)  # create|update|delete
    oude_waarde: Mapped[dict | None] = mapped_column(JSONB)
    nieuwe_waarde: Mapped[dict | None] = mapped_column(JSONB)
