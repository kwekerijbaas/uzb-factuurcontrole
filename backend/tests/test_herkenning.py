"""Tests voor de controle dat een bestand bij het gekozen uitzendbureau hoort.

Aanleiding: het was mogelijk Level One te kiezen en de bestanden van Sterk Werk
te uploaden. De app rekende die uren dan af tegen de tarieven van Level One en
bewaarde dat ook nog als weekresultaat -- een overzicht dat er compleet uitziet
maar volledig onjuist is.
"""

import pytest

from app.services.ingest.herkenning import controleer_uzb, herken_uzb
from app.services.ingest.snoop import SnoopMedewerker

NAMEN = {
    "L1": "Level One",
    "L1_JEUGD": "Level One jeugd-payroll",
    "SW": "Sterk Werk",
    "CK": "Cervokordaat",
}


def _mw(loonschaal=None, werkgever=None):
    return SnoopMedewerker(naam="Marius Mic", loonschaal=loonschaal, werkgever=werkgever)


@pytest.mark.parametrize(
    "werkgever,verwacht",
    [("Level One", "L1"), ("SterkWerk", "SW"), ("Sterk Werk", "SW"),
     ("Cervokordaat", "CK"), ("Onbekend BV", None),
     # Zoals SNOOP ze werkelijk schrijft; de langste passende naam wint, anders
     # zou jeugd-payroll als regulier Level One worden gelezen.
     ("Level One Payroll", "L1_JEUGD"), ("Level One Payroll Jeugd", "L1_JEUGD")],
)
def test_werkgeverskolom_bepaalt_het_bureau(werkgever, verwacht):
    sleutel, _ = herken_uzb([_mw(werkgever=werkgever)])
    assert sleutel == verwacht


@pytest.mark.parametrize(
    "loonschaal,verwacht", [("B2 Sw", "SW"), ("B2 Flex", "L1"), ("B4 Vast", "L1")]
)
def test_zonder_werkgeverskolom_telt_de_loonschaal(loonschaal, verwacht):
    sleutel, _ = herken_uzb([_mw(loonschaal=loonschaal)])
    assert sleutel == verwacht


def test_bestand_van_het_verkeerde_bureau_wordt_geweigerd():
    medewerkers = [_mw(loonschaal="B2 Sw", werkgever="SterkWerk")]
    with pytest.raises(ValueError) as fout:
        controleer_uzb(medewerkers, "L1", NAMEN)
    melding = str(fout.value)
    assert "Sterk Werk" in melding  # wat het is
    assert "Level One" in melding  # wat er gekozen was


def test_juiste_bureau_gaat_gewoon_door():
    controleer_uzb([_mw(werkgever="SterkWerk")], "SW", NAMEN)


def test_level_one_en_jeugd_delen_hun_bestanden():
    """Jeugd en payroll komen uit dezelfde SNOOP-export als het reguliere deel."""
    controleer_uzb([_mw(werkgever="Level One")], "L1_JEUGD", NAMEN)
    controleer_uzb([_mw(loonschaal="C6 Payroll")], "L1", NAMEN)


def test_onherkenbaar_bestand_blokkeert_niet():
    """Zonder aanwijzing geen loos alarm; de overige controles vangen dat af."""
    controleer_uzb([_mw()], "L1", NAMEN)


def test_factuur_van_het_verkeerde_bureau_wordt_geweigerd():
    import glob

    from app.services.ingest.factuur import lees_factuur

    pad = glob.glob(
        "/root/.claude/uploads/**/*Sterk_werk*PurchaseInvoice.pdf", recursive=True
    )
    if not pad:
        pytest.skip("voorbeeldfactuur niet beschikbaar")
    with pytest.raises(ValueError, match="opmaak van SW"):
        lees_factuur(pad[0], "L1")


# --------------------------------------------------------------------------- #
# Bureau afleiden in plaats van laten kiezen
# --------------------------------------------------------------------------- #
def test_bureau_wordt_uit_het_bestand_afgeleid():
    from app.services.ingest.herkenning import bepaal_uzb

    assert bepaal_uzb([_mw(werkgever="SterkWerk")], NAMEN) == "SW"
    assert bepaal_uzb([_mw(werkgever="Level One")], NAMEN) == "L1"


def test_zonder_werkgeverskolom_telt_de_loonschaal_ook_hier():
    from app.services.ingest.herkenning import bepaal_uzb

    assert bepaal_uzb([_mw(loonschaal="B2 Sw")], NAMEN) == "SW"
    assert bepaal_uzb([_mw(loonschaal="C6 Payroll")], NAMEN) == "L1_JEUGD"


def test_onbekende_werkgeversnaam_valt_terug_op_de_loonschaal():
    """Eén hernoemd bureau mag het bestand niet onbruikbaar maken."""
    from app.services.ingest.herkenning import bepaal_uzb

    assert bepaal_uzb([_mw(werkgever="Uitzendbureau XYZ", loonschaal="B2 Sw")], NAMEN) == "SW"


def test_jeugd_export_houdt_zijn_eigen_tariefkaart():
    """De jeugd-export is een apart bestand met eigen tarieven; als 'L1'
    verwerken zou de jeugduren tegen volwassentarieven waarderen."""
    from app.services.ingest.herkenning import bepaal_uzb

    jeugd = [_mw(werkgever="Level One Payroll Jeugd", loonschaal="B 17 jaar Jeugd")]
    assert bepaal_uzb(jeugd, NAMEN) == "L1_JEUGD"
    # In één export samen blijft het een Level One-bestand.
    gemengd = jeugd + [_mw(werkgever="Level One", loonschaal="B2 Flex")]
    assert bepaal_uzb(gemengd, NAMEN) == "L1"


def test_export_met_twee_bureaus_wordt_geweigerd():
    """Anders zou het ene bureau tegen de tarieven van het andere lopen."""
    from app.services.ingest.herkenning import bepaal_uzb

    gemengd = [_mw(werkgever="Level One"), _mw(werkgever="SterkWerk")]
    with pytest.raises(ValueError, match="meerdere uitzendbureaus"):
        bepaal_uzb(gemengd, NAMEN)


def test_bestand_zonder_aanwijzing_vraagt_om_de_kolom():
    from app.services.ingest.herkenning import bepaal_uzb

    with pytest.raises(ValueError, match="Werkgever op datum shift"):
        bepaal_uzb([_mw()], NAMEN)
