"""Arabic text in generated PDFs: one bundled font, one shaping path.

ReportLab can draw a TrueType font without a helper library, but it draws the characters as they are
stored — which for Arabic produces disconnected letters in logical order, i.e. text that a reader
cannot read. Correct Arabic needs two things:

1. **Shaping** (contextual letter forms, ligatures, mark positioning), which ReportLab performs through
   HarfBuzz when the font is registered as shapable and the string is passed as a `ShapedStr`;
2. **A font the PDF can embed**, so the document looks the same on a machine that has no Arabic font
   installed.

Both are provided here. The font is Noto Naskh Arabic (Regular + Bold), licensed under the SIL Open Font
License 1.1 — the license file ships next to the fonts and must stay with them. Shaping needs the
`uharfbuzz` wheel, which is a declared dependency; if it is somehow unavailable we do not pretend:
`arabic_ready()` reports false and callers fall back to the Latin-only layout with a logged warning
rather than emitting unreadable Arabic.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("procureai.pdf")

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
ARABIC_FONT = "NotoNaskhArabic"
ARABIC_FONT_BOLD = "NotoNaskhArabic-Bold"
_FILES = {
    ARABIC_FONT: "NotoNaskhArabic-Regular.ttf",
    ARABIC_FONT_BOLD: "NotoNaskhArabic-Bold.ttf",
}

#: Arabic, Arabic Supplement, Arabic Extended-A and the presentation forms.
_ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)

_registered = False
_ready = False


def has_arabic(text: str) -> bool:
    """True when the string contains at least one Arabic character."""
    return any(_is_arabic(char) for char in text or "")


def _is_arabic(char: str) -> bool:
    return any(low <= ord(char) <= high for low, high in _ARABIC_RANGES)


def _is_neutral(char: str) -> bool:
    """Spaces and punctuation take the direction of the paragraph, not of their own script."""
    return not char.isalnum()


def display_runs(text: str) -> list[tuple[str, bool]]:
    """Split a line into drawable runs, in **visual order left to right**.

    Arabic and Latin cannot share one shape call: HarfBuzz shapes one direction per buffer, so a mixed
    run would reverse the ASCII along with the Arabic (this is how Arabic PDFs end up with mirrored
    prices and part numbers). This is the small subset of the Unicode bidirectional algorithm a
    document line needs: the paragraph direction comes from the first strong character, and the runs
    are emitted in visual order — reversed for a right-to-left paragraph, in place for a left-to-right
    one. Neutral characters stay with the run they follow.

    Returns ``[(run_text, is_arabic_run), ...]`` in visual order; the caller draws them sequentially,
    picking the Arabic font for the Arabic runs, and takes the alignment from :func:`paragraph_is_rtl`.
    """
    if not text:
        return [("", False)]
    paragraph_rtl = paragraph_is_rtl(text)
    runs: list[tuple[str, bool]] = []
    for char in text:
        kind = _is_arabic(char)
        if runs and (kind == runs[-1][1] or _is_neutral(char)):
            runs[-1] = (runs[-1][0] + char, runs[-1][1])
        else:
            runs.append((char, kind))
    if paragraph_rtl:
        runs.reverse()
    return runs


def paragraph_is_rtl(text: str) -> bool:
    """The paragraph direction: the script of the first strong (letter) character."""
    first_strong = next((char for char in text or "" if char.isalpha()), "")
    return _is_arabic(first_strong)


def arabic_ready() -> bool:
    """Whether this installation can render shaped Arabic (fonts present **and** HarfBuzz importable)."""
    _register()
    return _ready


def _register() -> None:
    """Register the bundled faces once per process. Never raises: a missing font is a log line."""
    global _registered, _ready
    if _registered:
        return
    _registered = True
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except Exception as exc:  # pragma: no cover - ReportLab is a hard dependency
        log.warning("Arabic PDF font unavailable: reportlab not importable (%s)", exc)
        return
    try:
        import uharfbuzz  # noqa: F401  shaping engine behind reportlab's ShapedStr support
        shaping = True
    except Exception:  # pragma: no cover - the wheel is a declared dependency
        shaping = False
        log.warning("Arabic PDF text will not be shaped: uharfbuzz is not installed; PDFs stay Latin-only")
    registered = 0
    for name, filename in _FILES.items():
        path = FONT_DIR / filename
        if not path.exists():  # pragma: no cover - the fonts ship with the package
            log.warning("Arabic PDF font missing: %s", path)
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, str(path), shapable=shaping))
        except Exception as exc:  # pragma: no cover - corrupt font file
            log.warning("Arabic PDF font %s could not be registered: %s", filename, exc)
            continue
        registered += 1
    _ready = shaping and registered == len(_FILES)


def font_for(text: str, *, bold: bool = False) -> str:
    """The font that can draw this string: the bundled Arabic face, or Helvetica for Latin-only text."""
    if has_arabic(text) and arabic_ready():
        return ARABIC_FONT_BOLD if bold else ARABIC_FONT
    return "Helvetica-Bold" if bold else "Helvetica"


def layout(text: str, size: float, *, bold: bool = False) -> list[tuple[str, str, float]]:
    """The drawable runs of a line in visual order, each with its font and its measured width.

    A run that needs Arabic but cannot get it (no shaping engine) is drawn with Helvetica instead:
    unreadable Arabic is worse than honest boxes, and the log says so.
    """
    from reportlab.pdfbase import pdfmetrics

    ready = arabic_ready()
    latin = "Helvetica-Bold" if bold else "Helvetica"
    arabic = ARABIC_FONT_BOLD if bold else ARABIC_FONT
    out: list[tuple[str, str, float]] = []
    for run, is_arabic in display_runs(text):
        font = arabic if (is_arabic and ready) else latin
        value = shaped(run, font, size)
        out.append((run, font, pdfmetrics.stringWidth(value, font, size)))
    return out


def shaped(text: str, font_name: str, size: float) -> object:
    """Return the string ReportLab must receive so that Arabic is shaped (a no-op for Latin)."""
    if not font_name.startswith(ARABIC_FONT):
        return text
    _register()
    try:
        from reportlab.pdfbase.ttfonts import shapeStr

        return shapeStr(text, font_name, size)
    except Exception as exc:  # pragma: no cover - defensive: never fail a PO export over this
        log.warning("Arabic shaping failed, drawing unshaped text: %s", exc)
        return text
