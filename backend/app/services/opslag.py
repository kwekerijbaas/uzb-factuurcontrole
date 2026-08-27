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

from app.models import (
    CaoLoon,
    CaoLoontabel,
    Uzb,
    UzbTariefFactor,
    UzbTariefHandmatig,
    Uzk,
)
from app.services.tarief.kaart import Loontabel, TariefFactor, lonen_op


# --------------------------------------------------------------------------- #
# Loontabellen
# --------------------------------------------------------------------------- #
def verdwenen_schalen(sessie: Session, tabel: Loontabel) -> list[str]:
    """Welke schalen deze upload laat verdwijnen.

    Een upload met dezelfde ingangsdatum vervangt de hele tabel. Leest de
    inlezer een bestand maar half (de CAO-PDF van januari leverde 28 van de 87
    schalen op), dan verdwijnen de overige stilletjes -- en elke schaal zonder
    loon is een schaal zonder tarief. Aanroepen vóór `bewaar_loontabel`.
    """
    bestaand = sessie.scalar(
        select(CaoLoontabel).where(CaoLoontabel.ingangsdatum == tabel.ingangsdatum)
    )
    if bestaand is None:
        return []
    return sorted({loon.schaal_code for loon in bestaand.lonen} - set(tabel.lonen))


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
    """De lonen die op `dag` golden, per schaal.

    Opgebouwd uit alle tabellen tot en met die dag: een nieuwe tabel overschrijft
    alleen de schalen die hij zelf noemt (zie `lonen_op`). Gaat er per 1 juli
    alleen iets omhoog voor B1 en B2, dan houden de andere schalen hun loon in
    plaats van zonder tarief te komen zitten.
    """
    rijen = sessie.scalars(
        select(CaoLoontabel)
        .where(CaoLoontabel.ingangsdatum <= dag)
        .order_by(CaoLoontabel.ingangsdatum)
    ).all()
    return lonen_op([_naar_loontabel(r) for r in rijen], dag)


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
    volledig: bool = True,
) -> int:
    """Zet een nieuwe versie van de factoren weg vanaf `geldig_van`.

    Lopende versies worden afgesloten op de dag ervoor, zodat eerdere weken hun
    oorspronkelijke tarieven houden. Een al bestaande versie met exact dezelfde
    ingangsdatum wordt overschreven.

    `volledig=False` vervangt alleen de combinaties die in deze upload staan; de
    rest loopt door. Nodig omdat een uitzendbureau soms een export met alleen de
    gewijzigde schalen levert -- alles afsluiten zou dan iedere andere schaal
    vanaf die datum zonder tarief zetten, en dus zonder bedrag.
    """
    bestaand = sessie.scalars(
        select(UzbTariefFactor).where(UzbTariefFactor.uzb_id == uzb.id)
    ).all()
    vervangen = {(f.kaartcode, f.categorie) for f in factoren}

    for rij in bestaand:
        if not volledig and (rij.kaartcode, rij.categorie) not in vervangen:
            continue
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
# Handmatige tarieven
# --------------------------------------------------------------------------- #
def bewaar_handmatig_tarief(
    sessie: Session,
    uzb: Uzb,
    kaartcode: str,
    tarieven: dict[str, Decimal],
    geldig_van: date,
) -> int:
    """Leg handmatige tarieven vast voor één kaartschaal, per categorie.

    Een lopende regel voor dezelfde combinatie wordt afgesloten op de dag
    ervoor, zodat eerdere weken hun tarief houden; dezelfde ingangsdatum
    overschrijft.
    """
    bestaand = sessie.scalars(
        select(UzbTariefHandmatig)
        .where(UzbTariefHandmatig.uzb_id == uzb.id)
        .where(UzbTariefHandmatig.kaartcode == kaartcode)
    ).all()
    for rij in bestaand:
        if rij.categorie not in tarieven:
            continue
        if rij.geldig_van == geldig_van:
            sessie.delete(rij)
        elif rij.geldig_van < geldig_van and rij.geldig_tot is None:
            rij.geldig_tot = date.fromordinal(geldig_van.toordinal() - 1)
    sessie.flush()

    for categorie, tarief in tarieven.items():
        sessie.add(
            UzbTariefHandmatig(
                uzb_id=uzb.id,
                kaartcode=kaartcode,
                categorie=categorie,
                tarief=tarief,
                geldig_van=geldig_van,
                geldig_tot=None,
            )
        )
    sessie.flush()
    return len(tarieven)


def handmatige_tarieven_op(
    sessie: Session, uzb_sleutel: str, dag: date
) -> dict[str, dict[str, Decimal]]:
    """De handmatige tarieven die op `dag` gelden: kaartcode -> categorie -> tarief."""
    uzb = uzb_op_sleutel(sessie, uzb_sleutel)
    if uzb is None:
        return {}
    rijen = sessie.scalars(
        select(UzbTariefHandmatig)
        .where(UzbTariefHandmatig.uzb_id == uzb.id)
        .where(UzbTariefHandmatig.geldig_van <= dag)
        .where(
            (UzbTariefHandmatig.geldig_tot.is_(None))
            | (UzbTariefHandmatig.geldig_tot >= dag)
        )
    ).all()
    per_code: dict[str, dict[str, Decimal]] = {}
    for rij in rijen:
        per_code.setdefault(rij.kaartcode, {})[rij.categorie] = Decimal(str(rij.tarief))
    return per_code


def alle_handmatige_tarieven(sessie: Session) -> list[dict]:
    """Voor het beheerscherm: alle regels, lopend en beëindigd."""
    rijen = sessie.execute(
        select(UzbTariefHandmatig, Uzb.naam)
        .join(Uzb, Uzb.id == UzbTariefHandmatig.uzb_id)
        .order_by(Uzb.naam, UzbTariefHandmatig.kaartcode, UzbTariefHandmatig.geldig_van)
    ).all()
    return [
        {
            "id": rij.id,
            "uzb_sleutel": uzb_naam,
            "kaartcode": rij.kaartcode,
            "categorie": rij.categorie,
            "tarief": Decimal(str(rij.tarief)),
            "geldig_van": rij.geldig_van,
            "geldig_tot": rij.geldig_tot,
        }
        for rij, uzb_naam in rijen
    ]


def verwijder_handmatig_tarief(sessie: Session, tarief_id: uuid.UUID) -> bool:
    rij = sessie.get(UzbTariefHandmatig, tarief_id)
    if rij is None:
        return False
    sessie.delete(rij)
    sessie.flush()
    return True


def kaart_op(sessie: Session, uzb_sleutel: str, dag: date):
    """De tariefkaart die op `dag` geldt: afgeleid uit lonen x factoren, met de
    handmatige tarieven eroverheen.

    Handmatig wint van afgeleid: zo'n tarief is juist ingevoerd omdat de kaart
    het niet of fout heeft. Alle plekken die een kaart nodig hebben (week
    verwerken, schaal-toets bij Uitzendkrachten) horen déze functie te
    gebruiken, anders telt een handmatig tarief op de ene plek wel en op de
    andere niet.
    """
    from app.services.tarief import bouw_tariefkaart
    from app.services.tarief.types import SchaalTarief, TariefKaart

    lonen = loontabel_op(sessie, dag)
    factoren = factoren_op(sessie, uzb_sleutel, dag)
    kaart = None
    if lonen and factoren:
        kaart, _ = bouw_tariefkaart(uzb_sleutel, lonen, factoren)

    handmatig = handmatige_tarieven_op(sessie, uzb_sleutel, dag)
    if not handmatig:
        return kaart
    if kaart is None:
        kaart = TariefKaart(uzb_sleutel=uzb_sleutel, geldig_van=dag, schalen={})
    for kaartcode, tarieven in handmatig.items():
        bestaande = dict(kaart.schalen.get(kaartcode, SchaalTarief(kaartcode, {})).tarieven)
        bestaande.update(tarieven)
        kaart.schalen[kaartcode] = SchaalTarief(kaartcode, bestaande)
    return kaart


# --------------------------------------------------------------------------- #
# Uitzendkrachten en hun laatst bekende loonschaal
# --------------------------------------------------------------------------- #
def _net(naam: str) -> str:
    """Naam zoals we hem tonen: dubbele spaties eruit, hoofdletters behouden."""
    return re.sub(r"\s+", " ", str(naam or "")).strip()


def _sleutel(naam: str) -> str:
    """Naam om op te zoeken; SNOOP, Nitea en de lijsten verschillen in
    hoofdlettergebruik en spaties."""
    return _net(naam).lower()


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
    daarop terugvallen. Een lege schaal overschrijft nooit een bekende, en een
    handmatig ingevulde schaal wordt door een bestand niet overschreven -- dat
    kan alleen via een bewuste keuze in het scherm (`zet_loonschaal`).
    """
    rij = sessie.scalar(
        select(Uzk).where(Uzk.uzb_id == uzb.id).where(func.lower(Uzk.naam) == _sleutel(naam))
    )
    if rij is None:
        rij = Uzk(uzb_id=uzb.id, naam=_net(naam), externe_code=externe_code,
                  loonschaal_code=loonschaal, actief=True)
        sessie.add(rij)
        sessie.flush()
        return rij
    if externe_code and not rij.externe_code:
        rij.externe_code = externe_code
    if loonschaal and not rij.schaal_handmatig:
        rij.loonschaal_code = loonschaal
    if rij.naam != _net(naam) and rij.naam.islower():
        rij.naam = _net(naam)  # eerder in kleine letters bewaard
    return rij


def zet_loonschaal(rij: Uzk, loonschaal: str, handmatig: bool) -> None:
    """Bewuste schaalwijziging vanuit het scherm.

    `handmatig=True` bij een met de hand ingevulde waarde: die is daarna tegen
    bestanden beschermd. `handmatig=False` wanneer de gebruiker juist kiest de
    bestandswaarde over te nemen; daarmee vervalt de bescherming weer.
    """
    rij.loonschaal_code = loonschaal
    rij.schaal_handmatig = handmatig


def handmatige_loonschalen(sessie: Session, uzb_sleutel: str) -> dict[str, str]:
    """De met de hand ingevulde schalen, op genormaliseerde naam.

    Deze winnen bij het verwerken van een week van de SNOOP-waarde: ze zijn
    juist ingevuld omdat het bestand het fout of niet had.
    """
    uzb = uzb_op_sleutel(sessie, uzb_sleutel)
    if uzb is None:
        return {}
    rijen = sessie.scalars(
        select(Uzk)
        .where(Uzk.uzb_id == uzb.id)
        .where(Uzk.schaal_handmatig.is_(True))
        .where(Uzk.loonschaal_code.is_not(None))
    ).all()
    return {_sleutel(r.naam): r.loonschaal_code for r in rijen}


def bekende_loonschalen(sessie: Session, uzb_sleutel: str) -> dict[str, str]:
    """Laatst bekende loonschaal per uitzendkracht, op genormaliseerde naam."""
    uzb = uzb_op_sleutel(sessie, uzb_sleutel)
    if uzb is None:
        return {}
    rijen = sessie.scalars(
        select(Uzk).where(Uzk.uzb_id == uzb.id).where(Uzk.loonschaal_code.is_not(None))
    ).all()
    return {_sleutel(r.naam): r.loonschaal_code for r in rijen if r.loonschaal_code}


# --------------------------------------------------------------------------- #
# Weekresultaten bewaren en teruglezen
# --------------------------------------------------------------------------- #
def _opgeteld(regels, waarde):
    """Eén getal per tariefcategorie, ook als die categorie meerdere
    tariefperiodes kent."""
    totaal: dict = {}
    for regel in regels:
        totaal[regel.categorie] = totaal.get(regel.categorie, 0) + waarde(regel)
    return totaal


def bewaar_weekresultaat(sessie: Session, uzb: Uzb, verwerking) -> int:
    """Leg de uitkomst van een week vast.

    Hierdoor kan de factuur later los worden gecontroleerd, zonder SNOOP en
    Nitea opnieuw in te lezen -- de factuur komt immers dagen tot weken na de
    week binnen. Opnieuw verwerken van dezelfde week vervangt het resultaat.
    """
    from app.models import BerekendeUren, MatchPeriode

    bestaand = sessie.scalars(
        select(MatchPeriode)
        .join(Uzk, Uzk.id == MatchPeriode.uzk_id)
        .where(Uzk.uzb_id == uzb.id)
        .where(MatchPeriode.iso_jaar == verwerking.iso_jaar)
        .where(MatchPeriode.iso_week == verwerking.iso_week)
    ).all()
    for rij in bestaand:
        sessie.delete(rij)
    sessie.flush()

    for medewerker in verwerking.medewerkers:
        uzk = onthoud_uzk(
            sessie, uzb, medewerker.naam, medewerker.nitea_id, medewerker.loonschaal
        )
        match = MatchPeriode(
            uzk_id=uzk.id,
            iso_jaar=verwerking.iso_jaar,
            iso_week=verwerking.iso_week,
            status="gevalideerd",
            afwijkingen=[
                {"datum": a.datum.isoformat(), "soort": a.soort, "detail": a.detail}
                for a in medewerker.afwijkingen
            ],
        )
        sessie.add(match)
        sessie.flush()
        sessie.add(
            BerekendeUren(
                match_id=match.id,
                netto_minuten=medewerker.resultaat.netto_minuten,
                minuten_per_percentage={
                    str(pct): minuten
                    for pct, minuten in medewerker.resultaat.minuten_per_percentage.items()
                },
                loonschaal=medewerker.loonschaal,
                kaartcode=medewerker.kaartcode,
                # Optellen, niet overschrijven: gaat er midden in de week
                # een nieuwe loontabel in, dan heeft één categorie twee regels
                # met verschillende tarieven.
                minuten_per_categorie=_opgeteld(
                    medewerker.bedrag.regels, lambda r: r.minuten
                ),
                bedrag_per_categorie={
                    categorie: str(bedrag)
                    for categorie, bedrag in _opgeteld(
                        medewerker.bedrag.regels, lambda r: r.bedrag
                    ).items()
                },
                bedrag_totaal=medewerker.bedrag.totaal,
            )
        )
    sessie.flush()
    return len(verwerking.medewerkers)


def verwijder_weekresultaat(
    sessie: Session, uzb_sleutel: str, iso_jaar: int, iso_week: int
) -> int:
    """Verwijder een bewaarde week, bv. per ongeluk onder het verkeerde
    weeknummer verwerkt. Retourneert het aantal verwijderde medewerkers."""
    from app.models import MatchPeriode

    uzb = uzb_op_sleutel(sessie, uzb_sleutel)
    if uzb is None:
        return 0
    rijen = sessie.scalars(
        select(MatchPeriode)
        .join(Uzk, Uzk.id == MatchPeriode.uzk_id)
        .where(Uzk.uzb_id == uzb.id)
        .where(MatchPeriode.iso_jaar == iso_jaar)
        .where(MatchPeriode.iso_week == iso_week)
    ).all()
    for rij in rijen:
        sessie.delete(rij)
    sessie.flush()
    return len(rijen)


def bewaarde_weken(sessie: Session, uzb_sleutel: str | None = None) -> list[dict]:
    """Overzicht van de weken waarvan een resultaat is bewaard."""
    from app.models import BerekendeUren, MatchPeriode

    query = (
        select(
            Uzb.naam,
            MatchPeriode.iso_jaar,
            MatchPeriode.iso_week,
            func.count(MatchPeriode.id),
            func.sum(BerekendeUren.netto_minuten),
            func.sum(BerekendeUren.bedrag_totaal),
        )
        .join(Uzk, Uzk.id == MatchPeriode.uzk_id)
        .join(Uzb, Uzb.id == Uzk.uzb_id)
        .join(BerekendeUren, BerekendeUren.match_id == MatchPeriode.id)
        .group_by(Uzb.naam, MatchPeriode.iso_jaar, MatchPeriode.iso_week)
        .order_by(MatchPeriode.iso_jaar.desc(), MatchPeriode.iso_week.desc(), Uzb.naam)
    )
    if uzb_sleutel:
        query = query.where(Uzb.naam == uzb_sleutel)

    return [
        {
            "uzb_sleutel": naam,
            "iso_jaar": jaar,
            "iso_week": week,
            "medewerkers": aantal,
            "uren": (Decimal(minuten or 0) / 60).quantize(Decimal("0.01")),
            "bedrag": Decimal(str(bedrag or 0)),
        }
        for naam, jaar, week, aantal, minuten, bedrag in sessie.execute(query)
    ]


def haal_weekresultaat(sessie: Session, uzb_sleutel: str, iso_jaar: int, iso_week: int):
    """Herbouw een bewaarde week zodat de factuurcontrole ermee kan rekenen."""
    from app.models import BerekendeUren, MatchPeriode
    from app.services.calc.types import WeekResultaat
    from app.services.tarief.types import BedragRegel, BedragResultaat
    from app.services.verwerking import MedewerkerResultaat, WeekVerwerking

    uzb = uzb_op_sleutel(sessie, uzb_sleutel)
    if uzb is None:
        return None

    rijen = sessie.execute(
        select(Uzk, MatchPeriode, BerekendeUren)
        .join(MatchPeriode, MatchPeriode.uzk_id == Uzk.id)
        .join(BerekendeUren, BerekendeUren.match_id == MatchPeriode.id)
        .where(Uzk.uzb_id == uzb.id)
        .where(MatchPeriode.iso_jaar == iso_jaar)
        .where(MatchPeriode.iso_week == iso_week)
    ).all()
    if not rijen:
        return None

    verwerking = WeekVerwerking(uzb_sleutel, iso_jaar, iso_week)
    for uzk, _match, berekend in rijen:
        regels = [
            BedragRegel(
                categorie=categorie,
                bronnen=(),
                minuten=(berekend.minuten_per_categorie or {}).get(categorie, 0),
                tarief=Decimal("0"),
                bedrag=Decimal(str(bedrag)),
            )
            for categorie, bedrag in (berekend.bedrag_per_categorie or {}).items()
        ]
        verwerking.medewerkers.append(
            MedewerkerResultaat(
                naam=uzk.naam,
                nitea_id=uzk.externe_code,
                loonschaal=berekend.loonschaal,
                kaartcode=berekend.kaartcode,
                resultaat=WeekResultaat(
                    netto_minuten=berekend.netto_minuten,
                    minuten_per_percentage={},
                ),
                bedrag=BedragResultaat(regels=regels),
            )
        )
    verwerking.medewerkers.sort(key=lambda m: m.naam)
    return verwerking


def ruim_oude_weken_op(
    sessie: Session,
    jaren: int,
    vandaag: date | None = None,
    behoud: tuple[int, int] | None = None,
) -> int:
    """Verwijder weekresultaten ouder dan de bewaartermijn.

    Wordt aangeroepen bij het verwerken van een week, zodat er geen aparte
    schoonmaaktaak nodig is. Factuurregels die naar zo'n week verwijzen laten
    hun koppeling los in plaats van de verwijdering te blokkeren.

    `behoud` beschermt de zojuist verwerkte week. Zonder die uitzondering zou
    het narekenen van een oude week -- bijvoorbeeld bij een controle achteraf --
    een resultaat opleveren dat meteen weer wordt weggegooid.
    """
    from app.models import FactuurRegel, MatchPeriode

    grens = (vandaag or date.today())
    grens = grens.replace(year=grens.year - jaren)
    grens_jaar, grens_week, _ = grens.isocalendar()

    te_oud = (MatchPeriode.iso_jaar < grens_jaar) | (
        (MatchPeriode.iso_jaar == grens_jaar) & (MatchPeriode.iso_week < grens_week)
    )
    if behoud is not None:
        jaar, week = behoud
        te_oud = te_oud & ~(
            (MatchPeriode.iso_jaar == jaar) & (MatchPeriode.iso_week == week)
        )

    matches = sessie.scalars(select(MatchPeriode).where(te_oud)).all()
    if not matches:
        return 0

    ids = {m.id for m in matches}
    for regel in sessie.scalars(
        select(FactuurRegel).where(FactuurRegel.match_id.in_(ids))
    ).all():
        regel.match_id = None
    sessie.flush()

    for match in matches:
        sessie.delete(match)
    sessie.flush()
    return len(matches)
