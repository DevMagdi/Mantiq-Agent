"""Deterministic money / quantity / date math. All arithmetic in Decimal, never float.

The LLM never computes a number: it proposes text, this module validates and computes
(NFR: "Money, quantities and dates are validated by code").
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

QUANT = Decimal("0.0001")
QTY_QUANT = Decimal("0.000001")

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

CURRENCY_SYMBOLS: dict[str, str] = {
    "egp": "EGP", "ج.م": "EGP", "جم": "EGP", "le": "EGP", "£e": "EGP", "e£": "EGP", "جنيه": "EGP",
    "usd": "USD", "$": "USD", "us$": "USD", "dollar": "USD", "دولار": "USD",
    "eur": "EUR", "€": "EUR", "euro": "EUR", "يورو": "EUR",
    "sar": "SAR", "﷼": "SAR", "ریال": "SAR", "ريال": "SAR", "sr": "SAR",
    "aed": "AED", "د.إ": "AED", "aed$": "AED", "dirham": "AED", "درهم": "AED",
    "kwd": "KWD", "د.ك": "KWD", "dinar": "KWD",
    "qar": "QAR", "ر.ق": "QAR", "qatar riyal": "QAR",
    "cny": "CNY", "rmb": "CNY", "cny¥": "CNY", "元": "CNY",
    "try": "TRY", "₺": "TRY", "lira": "TRY",
    "inr": "INR", "₹": "INR", "rupee": "INR",
    "gbp": "GBP", "£": "GBP",
}

SUPPORTED_CURRENCIES = frozenset({"EGP", "USD", "EUR", "SAR", "AED", "KWD", "QAR", "GBP", "CNY", "TRY", "INR"})

CURRENCY_DECIMALS = {"KWD": 3, "BHD": 3, "OMR": 3, "JOD": 3, "IQD": 3, "LYD": 3, "TND": 3, "USD": 2,
                     "EUR": 2, "EGP": 2, "SAR": 2, "AED": 2, "QAR": 2, "GBP": 2, "CNY": 2, "TRY": 2,
                     "INR": 2}

UOM_ALIASES: dict[str, str] = {
    "ton": "t", "tons": "t", "tonne": "t", "tonnes": "t", "metric ton": "t", "طن": "t", "اطنان": "t",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilogram": "kg", "kilograms": "kg", "كجم": "kg", "كيلو": "kg",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb", "رطل": "lb",
    "m": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m", "متر": "m", "امتار": "m",
    "ft": "ft", "foot": "ft", "feet": "ft", "قدم": "ft",
    "pcs": "pcs", "pc": "pcs", "piece": "pcs", "pieces": "pcs", "unit": "pcs", "units": "pcs",
    "قطعة": "pcs", "قطع": "pcs", "حبة": "pcs", "وحدة": "pcs",
    "dozen": "doz", "doz": "doz", "درزن": "doz",
    "l": "l", "liter": "l", "litre": "l", "liters": "l", "litres": "l", "لتر": "l",
    "ml": "ml", "milliliter": "ml", "مل": "ml",
    "drum": "drum", "drums": "drum", "برميل": "drum",
    "bag": "bag", "bags": "bag", "شيكارة": "bag", "كيس": "bag",
    "roll": "roll", "rolls": "roll", "لفة": "roll", "رول": "roll",
    "m2": "m2", "sqm": "m2", "square meter": "m2", "متر مربع": "m2",
    "m3": "m3", "cbm": "m3", "cubic meter": "m3", "متر مكعب": "m3",
    "box": "box", "boxes": "box", "كرتونة": "box", "صندوق": "box",
    "set": "set", "sets": "set", "طقم": "set",
}

# dimension -> unit -> factor to the dimension's base unit
UNIT_TABLE: dict[str, dict[str, Decimal]] = {
    "mass": {"kg": Decimal("1"), "t": Decimal("1000"), "lb": Decimal("0.45359237"), "g": Decimal("0.001")},
    "length": {"m": Decimal("1"), "ft": Decimal("0.3048"), "cm": Decimal("0.01"), "mm": Decimal("0.001"),
               "km": Decimal("1000"), "in": Decimal("0.0254")},
    "volume": {"l": Decimal("1"), "ml": Decimal("0.001"), "m3": Decimal("1000"), "gal": Decimal("3.785411784")},
    "count": {"pcs": Decimal("1"), "doz": Decimal("12"), "box": Decimal("1"), "set": Decimal("1"),
              "bag": Decimal("1"), "roll": Decimal("1"), "drum": Decimal("1")},
}

UNIT_DIMENSION = {unit: dim for dim, units in UNIT_TABLE.items() for unit in units}


def normalize_digits(text: str) -> str:
    return (text or "").translate(ARABIC_DIGITS).translate(PERSIAN_DIGITS)


def normalize_uom(value: str | None) -> str:
    if not value:
        return "pcs"
    key = normalize_digits(str(value)).strip().lower().rstrip(".")
    return UOM_ALIASES.get(key, key[:16] or "pcs")


def parse_decimal(value: object) -> Decimal | None:
    """Parse money/quantity text into an exact Decimal.

    Handles Arabic-Indic digits and separators (``٢١٬٥٠٠٫٥``), thousands separators in either
    convention (``21,500.50`` / ``21.500,50``), unit suffixes (``2500 kg``, ``EGP 1,234.5/ton``)
    and currency symbols. Never uses floats.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, Decimal)):
        return Decimal(str(value))
    if isinstance(value, float):
        return Decimal(str(value))
    raw = normalize_digits(str(value)).replace("\u00a0", " ").replace("\u066c", "").replace("\u066b", ".")
    match = re.search(r"[-+]?\d[\d., ]*", raw)
    if not match:
        return None
    token = match.group(0).strip().replace(" ", "").rstrip(".,")
    if not token or token in {"-", "+"}:
        return None
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        parts = token.split(",")
        token = token.replace(",", ".") if len(parts) == 2 and 0 < len(parts[1]) <= 2 else token.replace(",", "")
    elif token.count(".") > 1:
        token = token.replace(".", "")
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def parse_percent(value: object) -> Decimal | None:
    if value is None:
        return None
    text = normalize_digits(str(value)).strip().replace("%", "").replace("٪", "")
    num = parse_decimal(text)
    if num is None:
        return None
    return num / 100 if num > 1 else num


def detect_currency(text: str, default: str = "EGP") -> str:
    """Detect an ISO currency from free text (AR/EN symbols and names)."""
    lowered = normalize_digits(text or "").lower()
    for token, iso in sorted(CURRENCY_SYMBOLS.items(), key=lambda kv: -len(kv[0])):
        token_l = token.lower()
        if token_l.isascii() and token_l.isalpha():
            if re.search(rf"\b{re.escape(token_l)}\b", lowered):
                return iso
        elif token_l in lowered:
            return iso
    for iso in CURRENCY_DECIMALS:
        if re.search(rf"\b{iso.lower()}\b", lowered):
            return iso
    return default


def money(value: Decimal | int | float | str | None, places: int = 4) -> Decimal:
    if value is None:
        return Decimal("0").quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP)
    num = parse_decimal(value) if not isinstance(value, Decimal) else value
    if num is None:
        num = Decimal("0")
    return num.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP)


def fmt_amount(value: Decimal | float | None, currency: str = "EGP", locale: str = "en") -> str:
    """Locale-aware currency formatting; KWD/QAR keep their mandated precision."""
    amount = money(value or 0, 2)
    decimals = CURRENCY_DECIMALS.get(currency.upper(), 2)
    amount = amount.quantize(Decimal(10) ** -decimals, rounding=ROUND_HALF_UP)
    formatted = f"{amount:,.{decimals}f}"
    if locale.startswith("ar"):
        return f"{formatted} {currency}"
    return f"{currency} {formatted}"


def unit_factor(from_unit: str, to_unit: str) -> Decimal | None:
    """Return the multiplicative factor converting from_unit into to_unit."""
    src = normalize_uom(from_unit)
    dst = normalize_uom(to_unit)
    if src == dst:
        return Decimal("1")
    dim_src = UNIT_DIMENSION.get(src)
    dim_dst = UNIT_DIMENSION.get(dst)
    if not dim_src or dim_src != dim_dst:
        return None
    return UNIT_TABLE[dim_src][src] / UNIT_TABLE[dim_src][dst]


def convert_quantity(quantity: Decimal, from_unit: str, to_unit: str) -> Decimal | None:
    factor = unit_factor(from_unit, to_unit)
    if factor is None:
        return None
    return (quantity * factor).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)


def convert_price(unit_price: Decimal, from_unit: str, to_unit: str) -> Decimal | None:
    """Price per from_unit expressed per to_unit (inverse of the quantity factor)."""
    factor = unit_factor(from_unit, to_unit)
    if factor is None or factor == 0:
        return None
    return (unit_price / factor).quantize(QUANT, rounding=ROUND_HALF_UP)


def fx_convert(amount: Decimal, rate: Decimal) -> Decimal:
    return (amount * rate).quantize(QUANT, rounding=ROUND_HALF_UP)


def compute_tco(
    subtotal: Decimal,
    *,
    fx_rate: Decimal = Decimal("1"),
    landed_components: list[tuple[str, Decimal]] | None = None,
    lead_time_days: int | None = None,
    cost_of_capital_annual: Decimal = Decimal("0.18"),
) -> dict:
    """Total cost of ownership in the buyer's base currency.

    Landed components are freight, duty, VAT, insurance, other (all in offer currency).
    Cost of capital for the payment delay between delivery and payment is included so
    quotes with different lead times compare fairly.
    """
    components = landed_components or []
    landed = sum((money(a) for _, a in components), Decimal("0"))
    base = fx_convert(money(subtotal), fx_rate)
    landed_base = fx_convert(landed, fx_rate)
    capital = Decimal("0")
    if lead_time_days and lead_time_days > 0:
        capital = (base * cost_of_capital_annual * Decimal(lead_time_days) / Decimal("365")).quantize(
            QUANT, rounding=ROUND_HALF_UP
        )
    tco = base + landed_base + capital
    return {
        "subtotal": money(subtotal),
        "fx_rate": Decimal(fx_rate),
        "landed_cost": money(landed),
        "landed_cost_base": money(landed_base),
        "cost_of_capital": money(capital),
        "total_base": money(base),
        "tco_total": money(tco),
        "components": [{"kind": k, "amount": money(v)} for k, v in components],
    }


DEFAULT_WEIGHTS = {"price": Decimal("0.40"), "lead_time": Decimal("0.20"), "quality": Decimal("0.20"),
                   "terms": Decimal("0.10"), "risk": Decimal("0.10")}


def weighted_scores(
    offers: list[dict],
    weights: dict[str, float | Decimal] | None = None,
) -> list[dict]:
    """Score offers 0..100 on each dimension and a weighted total.

    `offers` items need: id, tco_total, lead_time_days, supplier_quality (0-100),
    terms_score (0-100), risk_score (0-100). Price is scored relative to the cheapest TCO.
    """
    w = {k: Decimal(str(v)) for k, v in (weights or DEFAULT_WEIGHTS).items()}
    if not offers:
        return []
    total_w = sum(w.values()) or Decimal("1")
    w = {k: v / total_w for k, v in w.items()}
    cheapest = min(Decimal(str(o["tco_total"])) for o in offers) or Decimal("1")
    best_lead = min((o.get("lead_time_days") or 999) for o in offers)

    scored: list[dict] = []
    for offer in offers:
        tco = Decimal(str(offer["tco_total"])) or Decimal("1")
        price_score = (cheapest / tco * 100) if tco else Decimal("0")
        lead = offer.get("lead_time_days")
        lead_score = Decimal("100") if not lead else (Decimal(best_lead) / Decimal(lead) * 100)
        quality = Decimal(str(offer.get("supplier_quality") or 60))
        terms = Decimal(str(offer.get("terms_score") or 60))
        risk = Decimal(str(offer.get("risk_score") or 60))
        breakdown = {
            "price": round(float(price_score), 1),
            "lead_time": round(float(min(lead_score, Decimal("100"))), 1),
            "quality": round(float(quality), 1),
            "terms": round(float(terms), 1),
            "risk": round(float(risk), 1),
        }
        total = (
            price_score * w.get("price", Decimal("0"))
            + min(lead_score, Decimal("100")) * w.get("lead_time", Decimal("0"))
            + quality * w.get("quality", Decimal("0"))
            + terms * w.get("terms", Decimal("0"))
            + risk * w.get("risk", Decimal("0"))
        )
        scored.append({
            **offer,
            "score_breakdown": breakdown,
            "weighted_score": round(float(total), 2),
        })
    ranked = sorted(scored, key=lambda o: o["weighted_score"], reverse=True)
    for rank, item in enumerate(ranked, start=1):
        item["rank"] = rank
    return ranked


def savings(initial: Decimal | None, final: Decimal | None) -> dict:
    """Savings versus the first offer / estimate; percent is 0 when unknown."""
    initial = money(initial or 0)
    final = money(final or 0)
    delta = initial - final
    pct = (delta / initial * 100) if initial > 0 else Decimal("0")
    return {
        "initial": money(initial),
        "final": money(final),
        "savings": money(delta),
        "savings_pct": round(float(pct), 2),
    }


def resolve_relative_date(text: str, *, now: datetime | None = None, timezone: str = "Africa/Cairo") -> datetime | None:
    """Resolve '3 weeks', 'بعد أسبوعين', 'خلال 10 أيام', 'end of month' in code, not in the LLM."""
    base = now or datetime.now(UTC)
    lowered = normalize_digits(text or "").lower()
    if not lowered:
        return None
    arabic_words = {
        "يوم": 1, "أيام": 1, "ايام": 1, "أسبوع": 7, "اسبوع": 7, "أسابيع": 7, "اسابيع": 7,
        "شهر": 30, "أشهر": 30, "اشهر": 30, "سنة": 365, "سنه": 365,
    }
    word_numbers = {"يومين": 2, "أسبوعين": 2, "اسبوعين": 2, "شهرين": 2, "أسبوع": 1, "اسبوع": 1, "شهر": 1}
    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
    }
    match = re.search(r"(\d+(?:\.\d+)?)\s*(day|days|week|weeks|month|months|year|years|يوم|أيام|ايام|أسبوع|اسبوع|أسابيع|اسابيع|شهر|أشهر|اشهر)", lowered)
    if match:
        amount = Decimal(match.group(1))
        unit = match.group(2)
        days = Decimal(arabic_words.get(unit, 1)) if unit in arabic_words else {
            "day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30, "year": 365, "years": 365,
        }[unit]
        return (base + timedelta(days=float(amount * days))).replace(microsecond=0)
    for word, value in word_numbers.items():
        if word in lowered:
            return (base + timedelta(days=value * 7 if value == 2 and word.startswith(("أسبوع", "اسبوع")) else value * (7 if "أسبوع" in word or "اسبوع" in word else 30))).replace(microsecond=0)
    if "أسبوعين" in lowered or "اسبوعين" in lowered:
        return base + timedelta(days=14)
    if "يومين" in lowered:
        return base + timedelta(days=2)
    if "شهرين" in lowered:
        return base + timedelta(days=60)
    for name, idx in weekdays.items():
        if name in lowered:
            delta = (idx - base.weekday()) % 7 or 7
            return base + timedelta(days=delta)
    if "end of month" in lowered or "آخر الشهر" in lowered or "نهاية الشهر" in lowered:
        next_month = (base.replace(day=28) + timedelta(days=4)).replace(day=1)
        return next_month - timedelta(days=1)
    iso = re.search(r"\d{4}-\d{2}-\d{2}", lowered)
    if iso:
        return datetime.fromisoformat(iso.group(0)).replace(tzinfo=UTC)
    return None


def days_between(start: datetime | date | None, end: datetime | date | None) -> int | None:
    if not start or not end:
        return None
    if isinstance(start, datetime):
        start = start.date()
    if isinstance(end, datetime):
        end = end.date()
    return (end - start).days


def pct_change(old: Decimal | float | None, new: Decimal | float | None) -> float:
    old_d = Decimal(str(old or 0))
    new_d = Decimal(str(new or 0))
    if old_d == 0:
        return 0.0
    return round(float((new_d - old_d) / old_d * 100), 2)
