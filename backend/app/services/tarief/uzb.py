"""Facturatie-conventies per uitzendbureau (SPEC §5).

Elke UZB rekent dezelfde CAO-uren anders af. De verschillen zitten in twee
dingen: hoe de SNOOP-loonschaal naar de kaartcode vertaalt, en welke
tariefkolom bij welke toeslag-bron hoort.
"""

from __future__ import annotations

from .types import (
    CAT_100,
    CAT_135,
    CAT_150,
    CAT_200,
    CAT_FEESTDAG,
    CAT_NACHTUUR,
    UzbConventies,
)

# Bronnen zoals de calc-engine ze in de trace zet.
_BRON_NORMAAL = "normaal"
_BRON_OVERWERK = "overwerk_35"
_BRON_DAG_GRENS = "dag_grens_50"
_BRON_WEEK_GRENS = "week_grens_50"
_BRON_NACHT = "nacht"
_BRON_AVOND = "avond"
_BRON_ZATERDAG = "zaterdag_middag"
_BRON_ZONDAG = "zondag"
_BRON_FEESTDAG = "feestdag"


# Level One (regulier, Volwassenen/Payroll en Jeugd) — 135% gaat tegen het
# basistarief, feestdag heeft een eigen kolom.
LEVEL_ONE = UzbConventies(
    sleutel="L1",
    naam="Level One",
    bron_naar_categorie={
        _BRON_NORMAAL: CAT_100,
        _BRON_OVERWERK: CAT_100,
        _BRON_NACHT: CAT_150,
        _BRON_AVOND: CAT_150,
        _BRON_ZATERDAG: CAT_150,
        _BRON_DAG_GRENS: CAT_150,
        _BRON_WEEK_GRENS: CAT_150,
        _BRON_ZONDAG: CAT_200,
        _BRON_FEESTDAG: CAT_FEESTDAG,
    },
    # "B2 Flex" -> B2F, "B4 Vast" -> B4V, "C2 Seizoens" -> C2S,
    # "C6 Payroll" -> C6V (payroll deelt het Vast-tarief)
    code_regels=(
        (r"^(\S+)\s+Flex$", r"\1F"),
        (r"^(\S+)\s+Vast$", r"\1V"),
        (r"^(\S+)\s+(?:Payroll)$", r"\1V"),
        (r"^(\S+)\s+Seizoens(?:krachten)?$", r"\1S"),
    ),
    dag_grens_factureren=True,
)

# Level One jeugd-payroll — dezelfde toeslagafspraken als regulier Level One,
# maar een eigen tariefkaart met een tarief per leeftijd. SNOOP schrijft de
# schaal daar voluit ("B 17 jaar Jeugd"), de kaart als "17B2".
LEVEL_ONE_JEUGD = UzbConventies(
    sleutel="L1_JEUGD",
    naam="Level One jeugd-payroll",
    bron_naar_categorie=dict(LEVEL_ONE.bron_naar_categorie),
    code_regels=(
        (r"^([A-Za-z])\s*(\d{1,2})\s*jaar(?:\s+payroll)?\s+jeugd$", r"\2\g<1>2"),
        *LEVEL_ONE.code_regels,
    ),
    dag_grens_factureren=LEVEL_ONE.dag_grens_factureren,
)

# Sterk Werk — geen dag-grens doorbelasten, feestdag als 150%, en een apart
# tarief voor nachtdiensturen ('Totaal nachtuur').
STERK_WERK = UzbConventies(
    sleutel="SW",
    naam="Sterk Werk",
    bron_naar_categorie={
        _BRON_NORMAAL: CAT_100,
        _BRON_OVERWERK: CAT_100,
        _BRON_NACHT: CAT_NACHTUUR,
        _BRON_AVOND: CAT_150,
        _BRON_ZATERDAG: CAT_150,
        _BRON_WEEK_GRENS: CAT_150,
        _BRON_ZONDAG: CAT_200,
        _BRON_FEESTDAG: CAT_150,
    },
    # "B2 Sw" -> B2 (suffix strippen)
    code_regels=((r"^(\S+)\s+Sw$", r"\1"),),
    dag_grens_factureren=False,
)

# Cervokordaat — 100% en 135% zijn aparte kolommen; geen nachtdiensten.
CERVOKORDAAT = UzbConventies(
    sleutel="CK",
    naam="Cervokordaat",
    bron_naar_categorie={
        _BRON_NORMAAL: CAT_100,
        _BRON_OVERWERK: CAT_135,
        _BRON_NACHT: CAT_150,
        _BRON_AVOND: CAT_150,
        _BRON_ZATERDAG: CAT_150,
        _BRON_DAG_GRENS: CAT_150,
        _BRON_WEEK_GRENS: CAT_150,
        _BRON_ZONDAG: CAT_200,
        _BRON_FEESTDAG: CAT_150,
    },
    code_regels=(),  # identity: "C4" -> C4
    dag_grens_factureren=True,
)

CONVENTIES: dict[str, UzbConventies] = {
    c.sleutel: c for c in (LEVEL_ONE, LEVEL_ONE_JEUGD, STERK_WERK, CERVOKORDAAT)
}


def conventies(sleutel: str) -> UzbConventies:
    try:
        return CONVENTIES[sleutel]
    except KeyError:  # pragma: no cover - defensief
        raise ValueError(f"onbekende UZB-sleutel: {sleutel}") from None
