"""Lezen en schrijven van loontabellen en omrekenfactoren in de database.

Vertaalt tussen de SQLAlchemy-modellen en de pure domeintypes die de
tarief-service gebruikt, zodat de rekenlogica databasevrij blijft.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CaoLoon, CaoLoontabel, Uzb, UzbTariefFactor, Uzk
from app.services.tarief.kaart import Loontabel, TariefFactor


# --------------------------------------------------------------------------- #
# Loontabellen
# --------------------------------------------------------------------------- #
def bewaar_loontabel(
    sessie: Session,
    tabel: Loontabel,
    bron_bestand: str | None = None,
    door: uuid.UUID | None = None,
) -> CaoLoontabel:
    """Sla een loontabel op. Een bestaande tabel met dezelfde ingangsdatum
    wordt vervangen -- opnieuw uploaden corrigeert dus, in plaats van te
    stapelen."""
    bestaand = sessie.scalar(
        select(CaoLoontabel).where(CaoLoontabel.ingangsdatum == tabel.ingangsdatum)
    )
    if bestaand is not None:
        sessie.delete(bestaand)
        sessie.flush()

    rij = CaoLoontabel(
        naam=tabel.naam,
        ingangsdatum=tabel.ingangsdatum,
        bron_bestand=bron_bestand,
        geimporteerd_door=door,
    )
    rij.lonen = [
        CaoLoon(schaal_code=code, uurloon=loon) for code, loon in sorted(tabel.lonen.items())
    ]
    sessie.add(rij)
    sessie.flush()
    return rij


def _naar_loontabel(rij: CaoLoontabel) -> Loontabel:
    return Loontabel(
        naam=rij.naam,
        ingangsdatum=rij.ingangsdatum,
        lonen={loon.schaal_code: Decimal(str(loon.uurloon)) for loon in rij.lonen},
    )


def loontabellen(sessie: Session) -> list[Loontabel]:
    rijen = sessie.scalars(
        select(CaoLoontabel).order_by(CaoLoontabel.ingangsdatum)
    ).all()
    return [_naar_loontabel(r) for r in rijen]


def loontabel_op(sessie: Session, dag: date) -> Loontabel | None:
    """De loontabel die op `dag` gold: de laatste met ingangsdatum <= dag."""
    rij = sessie.scalar(
        select(CaoLoontabel)
        .where(CaoLoontabel.ingangsdatum <= dag)
        .order_by(CaoLoontabel.ingangsdatum.desc())
        .limit(1)
    )
    return _naar_loontabel(rij) if rij else None


# --------------------------------------------------------------------------- #
# Omrekenfactoren (SCD2)
# --------------------------------------------------------------------------- #
def uzb_op_sleutel(sessie: Session, sleutel: str) -> Uzb | None:
    return sessie.scalar(select(Uzb).where(Uzb.naam == sleutel))


def borg_uzb(sessie: Session, sleutel: str, naam: str | None = None) -> Uzb:
    rij = uzb_op_sleutel(sessie, sleutel)
    if rij is None:
        rij = Uzb(naam=sleutel, factuur_prefix=naam)
        sessie.add(rij)
        sessie.flush()
    return rij


def bewaar_factoren(
    sessie: Session,
    uzb: Uzb,
    factoren: list[TariefFactor],
    geldig_van: date,
) -> int:
    """Zet een nieuwe versie van de factoren weg vanaf `geldig_van`.

    Lopende versies worden afgesloten op de dag ervoor, zodat eerdere weken hun
    oorspronkelijke tarieven houden. Een al bestaande versie met exact dezelfde
    ingangsdatum wordt overschreven.
    """
    bestaand = sessie.scalars(
        select(UzbTariefFactor).where(UzbTariefFactor.uzb_id == uzb.id)
    ).all()

    for rij in bestaand:
        if rij.geldig_van == geldig_van:
            sessie.delete(rij)
        elif rij.geldig_van < geldig_van and rij.geldig_tot is None:
            rij.geldig_tot = date.fromordinal(geldig_van.toordinal() - 1)
    sessie.flush()

    for f in factoren:
        sessie.add(
            UzbTariefFactor(
                uzb_id=uzb.id,
                kaartcode=f.kaartcode,
                cao_schaal_code=f.cao_schaal_code,
                categorie=f.categorie,
                factor=f.factor,
                geldig_van=geldig_van,
                geldig_tot=None,
            )
        )
    sessie.flush()
    return len(factoren)


def factoren_op(sessie: Session, uzb_sleutel: str, dag: date) -> list[TariefFactor]:
    """De factoren die op `dag` golden voor dit uitzendbureau."""
    uzb = uzb_op_sleutel(sessie, uzb_sleutel)
    if uzb is None:
        return []
    rijen = sessie.scalars(
        select(UzbTariefFactor)
        .where(UzbTariefFactor.uzb_id == uzb.id)
        .where(UzbTariefFactor.geldig_van <= dag)
        .where(
            (UzbTariefFactor.geldig_tot.is_(None)) | (UzbTariefFactor.geldig_tot >= dag)
        )
    ).all()
    return [
        TariefFactor(
            kaartcode=r.kaartcode,
            cao_schaal_code=r.cao_schaal_code,
            categorie=r.categorie,
            factor=Decimal(str(r.factor)),
        )
        for r in rijen
    ]


# --------------------------------------------------------------------------- #
# Uitzendkrachten en hun laatst bekende loonschaal
# --------------------------------------------------------------------------- #
def _sleutel(naam: str) -> str:
    return re.sub(r"\s+", " ", str(naam or "")).strip().lower()


def onthoud_uzk(
    sessie: Session,
    uzb: Uzb,
    naam: str,
    externe_code: str | None = None,
    loonschaal: str | None = None,
) -> Uzk:
    """Leg de uitzendkracht vast met zijn loonschaal.

    De loonschaal komt uit SNOOP en staat daar niet altijd in: wie wel in Nitea
    zit maar niet in de planning, kreeg voorheen geen tarief en dus een bedrag
    van nul. Door de schaal per uitzendkracht te bewaren kan een volgende week
    daarop terugvallen. Een lege schaal overschrijft nooit een bekende.
    """
    rij = sessie.scalar(
        select(Uzk).where(Uzk.uzb_id == uzb.id).where(func.lower(Uzk.naam) == _sleutel(naam))
    )
    if rij is None:
        rij = Uzk(uzb_id=uzb.id, naam=_sleutel(naam), externe_code=externe_code,
                  loonschaal_code=loonschaal, actief=True)
        sessie.add(rij)
        sessie.flush()
        return rij
    if externe_code and not rij.externe_code:
        rij.externe_code = externe_code
    if loonschaal:
        rij.loonschaal_code = loonschaal
    return rij


def bekende_loonschalen(sessie: Session, uzb_sleutel: str) -> dict[str, str]:
    """Laatst bekende loonschaal per uitzendkracht, op genormaliseerde naam."""
    uzb = uzb_op_sleutel(sessie, uzb_sleutel)
    if uzb is None:
        return {}
    rijen = sessie.scalars(
        select(Uzk).where(Uzk.uzb_id == uzb.id).where(Uzk.loonschaal_code.is_not(None))
    ).all()
    return {_sleutel(r.naam): r.loonschaal_code for r in rijen if r.loonschaal_code}
