"""Kenyan phone number normalization.

All phone numbers are stored in international form without a plus sign
(e.g. 254712345678).
"""
import re

_NON_DIGITS = re.compile(r"[\s\-().]")

# Safaricom, Airtel, Telkom etc. mobile numbers start 07xx / 01xx.
_KE_MOBILE = re.compile(r"^254[17]\d{8}$")


def normalize_ke_phone(raw: str) -> str | None:
    """Return 2547XXXXXXXX / 2541XXXXXXXX or None if the number is invalid."""
    if not raw:
        return None
    value = _NON_DIGITS.sub("", str(raw))
    if value.startswith("+"):
        value = value[1:]
    if value.startswith("00"):
        value = value[2:]
    if value.startswith("0") and len(value) == 10:
        value = "254" + value[1:]
    elif len(value) == 9 and value[0] in "17":
        value = "254" + value
    if not value.isdigit():
        return None
    return value if _KE_MOBILE.match(value) else None
