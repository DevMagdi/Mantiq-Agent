"""Phone-number normalisation for the channels module.

A WhatsApp webhook hands us a `from`/`wa_id` that is usually E.164 digits without the `+`
(`201012345678`), while a user's profile may hold the Egyptian local form (`01012345678`),
the same number in E.164 (`+201012345678`), the international prefix (`0020…`) or a human
formatting with spaces and dashes. Comparing the raw strings never matches, which is exactly why a
message from a buyer's phone used to open nothing.

`normalise` returns one canonical representation — country code + subscriber digits, no `+`, no
punctuation — and `same_number` compares two values through it. The Egyptian cases are the ones that
matter for the pilot; other country codes are kept as given so an international number still matches
itself.
"""
from __future__ import annotations

import re

#: Egypt: country code 20 and ten national digits (`1` + nine). A local number starts with `0`.
EGYPT_CC = "20"
EGYPT_NATIONAL_LENGTH = 10

_NON_DIGITS = re.compile(r"\D+")


def digits_only(value: str | None) -> str:
    """Strip everything that is not a digit (spaces, dashes, parentheses, a leading `+`)."""
    return _NON_DIGITS.sub("", value or "")


def normalise(value: str | None, *, default_cc: str = EGYPT_CC) -> str:
    """Canonical `countrycode + subscriber` digits, or "" when there is nothing usable.

    Rules, in order:
    * `00` international prefix is dropped (`00201012345678` → `201012345678`);
    * an Egyptian **local** number (`01012345678`, eleven digits starting with `0`) gets the country
      code (`201012345678`);
    * any other leading `0` on an eleven-digit number is treated the same way (the local form of the
      default country);
    * everything else is returned as its digits, so a number with a country code matches itself.
    """
    raw = digits_only(value)
    if not raw:
        return ""
    if raw.startswith("00"):
        raw = raw[2:]
    if raw.startswith("0"):
        trimmed = raw.lstrip("0")
        # A local number is the national significant number with a single leading zero.
        if len(trimmed) == EGYPT_NATIONAL_LENGTH and default_cc:
            return f"{default_cc}{trimmed}"
        return trimmed or raw
    return raw


def same_number(left: str | None, right: str | None, *, default_cc: str = EGYPT_CC) -> bool:
    """True when two representations denote the same line."""
    a, b = normalise(left, default_cc=default_cc), normalise(right, default_cc=default_cc)
    if not a or not b:
        return False
    if a == b:
        return True
    # A stored number may omit the country code (older records, imports): compare the tail the way
    # the supplier matcher already does, but only when both are long enough to be meaningful.
    return len(a) >= 9 and len(b) >= 9 and a.endswith(b[-9:]) and b.endswith(a[-9:])
