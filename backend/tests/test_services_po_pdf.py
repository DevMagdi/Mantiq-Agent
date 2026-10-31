"""The purchase-order PDF must be readable in Arabic, everywhere, without a manual step.

What is proven here: the Noto Naskh Arabic face and its OFL licence ship inside the package, the face
is registered as *shapable* (ReportLab shapes Arabic through HarfBuzz), a PO with Arabic lines embeds
the subset font and produces shaped drawing operators, the run order of a mixed Arabic/ASCII line is
right-to-left while its ASCII stays in order, Latin-only lines keep Helvetica, and a missing font file
degrades to a PDF rather than an exception.

What cannot be proven here: that the rendered page *looks* right — this sandbox has no PDF rasteriser
(`pdftoppm`, `mutool`, `qpdf` are all absent). The sample written by `scripts/` and the manual step in
docs/RUNBOOK.md are the visual check; everything above the pixels is asserted below.
"""
from __future__ import annotations

import base64
import re
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import pdf_fonts
from app.services.po import po_pdf

ARABIC_LINE = {"description": "حديد تسليح 16 مم", "quantity": "20", "uom": "ton",
               "unit_price": "20,500.00", "line_total": "410,000.00"}
LATIN_LINE = {"description": "Cement bags 50 kg", "quantity": "120", "uom": "bag",
              "unit_price": "150.00", "line_total": "18,000.00"}


def _po(*lines: dict) -> SimpleNamespace:
    return SimpleNamespace(code="PO-2026-0001", amount="410,000.00", currency="EGP", lines=list(lines))


def _inflate(blob: bytes) -> bytes:
    """ReportLab compresses page streams (Flate) and may ASCII85-armour them first."""
    try:
        return zlib.decompress(blob)
    except zlib.error:
        pass
    try:
        return zlib.decompress(base64.a85decode(blob.strip(), adobe=True))
    except Exception:
        return blob


def _streams(pdf: bytes) -> list[bytes]:
    return [_inflate(match.group(1)) for match in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S)]


def _content(pdf: bytes) -> str:
    for stream in _streams(pdf):
        text = stream.decode("latin-1", "replace")
        if " Tj" in text or " TJ" in text:
            return text
    return ""


@pytest.fixture(autouse=True)
def _fresh_registration():
    """Font registration is process-global; each test starts from a clean slate and leaves one behind."""
    pdf_fonts._registered = False
    pdf_fonts._ready = False
    yield
    pdf_fonts._registered = False
    pdf_fonts._ready = False


# ------------------------------------------------------------------ what ships
def test_the_arabic_font_and_its_licence_ship_with_the_code():
    directory = Path(pdf_fonts.FONT_DIR)
    for filename in ("NotoNaskhArabic-Regular.ttf", "NotoNaskhArabic-Bold.ttf"):
        path = directory / filename
        assert path.exists(), f"{filename} must ship with the package (data files), not be downloaded"
        assert path.stat().st_size > 100_000, "a real face, not a placeholder"
    licence = (directory / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL Open Font License" in licence, "OFL-1.1 requires the licence to travel with the font"


def test_the_face_is_registered_as_shapable_and_shaping_returns_a_shaped_string():
    assert pdf_fonts.arabic_ready() is True
    from reportlab.pdfbase.ttfonts import ShapedStr

    shaped = pdf_fonts.shaped("حديد", pdf_fonts.ARABIC_FONT, 10)
    assert isinstance(shaped, ShapedStr), "drawing the raw string would produce disconnected letters"
    # Latin is not pushed through the shaper: Helvetica has no HarfBuzz face and needs none.
    assert pdf_fonts.shaped("Cement", "Helvetica", 10) == "Cement"


# ------------------------------------------------------------------ the document
def test_a_po_with_arabic_lines_embeds_the_font_and_draws_shaped_text():
    pdf = po_pdf("<html></html>", _po(ARABIC_LINE, LATIN_LINE))
    assert pdf.startswith(b"%PDF"), "not a PDF at all"
    assert len(pdf) > 10_000, len(pdf)
    assert b"/FontFile2" in pdf, "the font must be embedded, not referenced from the reader's machine"
    assert b"NotoNaskhArabic" in pdf, "the embedded subset must be the bundled Arabic face"
    content = _content(pdf)
    assert " TJ" in content, "no TJ operator: the text was drawn without shaping (kerning/mark placement)"
    assert re.search(r"/F\d+\+0 \d+ Tf", content), "no subset font resource in the content stream"


def test_a_latin_line_keeps_helvetica_and_is_drawn_verbatim():
    content = _content(po_pdf("<html></html>", _po(LATIN_LINE)))
    assert "(Cement bags 50 kg) Tj" in content, "Latin text must not be re-encoded through the Arabic font"
    assert "(120 bag  |  150.00 EGP  |  18,000.00) Tj" in content


# ------------------------------------------------------------------ ordering
def test_mixed_arabic_lines_are_ordered_right_to_left_with_ascii_left_intact():
    runs = pdf_fonts.display_runs("حديد تسليح 16 مم")
    assert runs == [("مم", True), ("16 ", False), ("حديد تسليح ", True)], runs
    assert pdf_fonts.paragraph_is_rtl("حديد تسليح 16 مم") is True
    assert pdf_fonts.paragraph_is_rtl("Cement 50 kg") is False
    assert pdf_fonts.display_runs("Cement 50 kg") == [("Cement 50 kg", False)]

    # Each run gets the font its script needs, and the widths are measured per run.
    layout = pdf_fonts.layout("حديد تسليح 16 مم", 9)
    assert [font for _, font, _ in layout] == [
        pdf_fonts.ARABIC_FONT, "Helvetica", pdf_fonts.ARABIC_FONT,
    ]
    assert all(width > 0 for _, _, width in layout)


def test_the_problematic_case_of_a_mirrored_price_is_kept_out_of_the_arabic_run():
    """"حديد 20,500 جنيه" must not hand the digits to the RTL shaper."""
    runs = pdf_fonts.display_runs("حديد 20,500 جنيه")
    numbers = [run for run, is_arabic in runs if not is_arabic]
    assert "20,500 " in numbers
    assert all("20,500" in run for run in numbers)


# ------------------------------------------------------------------ honesty when the font is absent
def test_a_missing_font_still_produces_a_pdf_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_fonts, "FONT_DIR", tmp_path)
    pdf_fonts._registered = False
    pdf_fonts._ready = False

    with patch.object(pdf_fonts.log, "warning") as warned:
        pdf = po_pdf("<html></html>", _po(ARABIC_LINE))
    assert pdf.startswith(b"%PDF")
    assert b"NotoNaskhArabic" not in pdf
    said = " ".join(str(call.args[0]) for call in warned.call_args_list)
    assert "Arabic PDF font missing" in said, "silent unreadable Arabic is the failure mode to avoid"
