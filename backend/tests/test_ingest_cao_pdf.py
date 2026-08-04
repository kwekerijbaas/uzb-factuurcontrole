"""Tests voor de CAO-loontabel uit de gepubliceerde PDF (SPEC §6).

Gevalideerd tegen de tabel per 1 augustus 2026: 80 schalen, ingangsdatum
automatisch herkend, en de treden 8 t/m 11 correct alleen onder de schalen
waar ze bestaan.
"""

from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.ingest.cao_pdf import lees_cao_pdf

_KOLOM_X = {"B": 220, "C": 300, "D": 380, "E": 460, "F": 540, "G": 620, "H": 700}


def _pdf(regels, kop="Loontabel cao Glastuinbouw geldend vanaf 1 augustus 2026") -> bytes:
    """Bouw een PDF met dezelfde kolomindeling als de echte publicatie."""
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    y = 780
    c.drawString(70, y, kop)
    y -= 40
    c.drawString(90, y, "Trede/schaal")
    for letter, x in _KOLOM_X.items():
        c.drawString(x, y, letter)
    for label, bedragen in regels:
        y -= 20
        c.drawString(110, y, label)
        for letter, bedrag in bedragen.items():
            c.drawString(_KOLOM_X[letter], y, bedrag)
    c.save()
    return buffer.getvalue()


VOLLEDIG = _pdf(
    [
        ("15 jaar", {"B": "6,00", "C": "6,16", "H": "7,56"}),
        ("1", {}),  # trede zonder bedragen
        ("2", {"B": "14,99", "C": "15,41", "H": "18,90"}),
        ("8", {"E": "19,65", "F": "20,73", "G": "21,94", "H": "23,91"}),
        ("11", {"H": "26,85"}),
    ]
)


def test_ingangsdatum_komt_uit_het_document():
    tabel, _ = lees_cao_pdf(VOLLEDIG)
    assert tabel.ingangsdatum == date(2026, 8, 1)
    assert "01-08-2026" in tabel.naam


def test_volwassen_en_jeugdschalen_krijgen_eigen_codes():
    tabel, _ = lees_cao_pdf(VOLLEDIG)
    assert tabel.loon("B2") == Decimal("14.99")  # letter + trede
    assert tabel.loon("15B") == Decimal("6.00")  # leeftijd + letter


def test_alleen_ingevulde_vakjes_worden_ingelezen():
    """Trede 1 staat wel in de tabel maar heeft geen bedragen; die mag dus geen
    enkele schaal opleveren."""
    tabel, _ = lees_cao_pdf(VOLLEDIG)
    assert not [code for code in tabel.lonen if code[1:] == "1"]
    assert tabel.loon("B1") is None
    assert tabel.loon("C15") is None  # jeugd staat als 15C, niet als C15


def test_rechts_uitgelijnde_treden_landen_in_de_juiste_kolom():
    """Trede 8 bestaat alleen bij E t/m H en staat daarom rechts uitgelijnd;
    op volgorde inlezen zou die bedragen bij B t/m E zetten."""
    tabel, _ = lees_cao_pdf(VOLLEDIG)
    assert tabel.loon("E8") == Decimal("19.65")
    assert tabel.loon("H8") == Decimal("23.91")
    assert tabel.loon("B8") is None
    assert tabel.loon("H11") == Decimal("26.85")
    assert tabel.loon("G11") is None


def test_opgegeven_datum_wint_maar_waarschuwt_bij_verschil():
    tabel, waarschuwingen = lees_cao_pdf(VOLLEDIG, ingangsdatum=date(2026, 9, 1))
    assert tabel.ingangsdatum == date(2026, 9, 1)
    assert any("wijkt af" in w for w in waarschuwingen)


def test_pdf_zonder_loontabel_wordt_geweigerd():
    leeg = _pdf([], kop="Nieuwsbrief cao Glastuinbouw")
    with pytest.raises(ValueError, match="geen loontabel herkend"):
        lees_cao_pdf(leeg)


def test_zonder_datum_in_document_is_die_verplicht():
    zonder = _pdf([("2", {"B": "14,99"})], kop="Loontabel cao Glastuinbouw")
    with pytest.raises(ValueError, match="ingangsdatum"):
        lees_cao_pdf(zonder)
    tabel, _ = lees_cao_pdf(zonder, ingangsdatum=date(2026, 8, 1))
    assert tabel.loon("B2") == Decimal("14.99")
