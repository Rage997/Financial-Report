"""Currency normalization and minor-unit precision for native-currency accounting.

Amounts are never converted: every transaction keeps the currency of its
source. A currency is identified by a supported ISO 4217 code or by a symbol
that names exactly one supported currency. Ambiguous symbols such as "$" or
"¥" are rejected rather than guessed, and unknown codes fail explicitly.
"""
from __future__ import annotations

# Supported ISO 4217 codes and their minor-unit fraction digits.
_FRACTION_DIGITS: dict[str, int] = {
    # zero minor units
    "BIF": 0, "CLP": 0, "DJF": 0, "GNF": 0, "ISK": 0, "JPY": 0, "KMF": 0,
    "KRW": 0, "PYG": 0, "RWF": 0, "UGX": 0, "VND": 0, "VUV": 0, "XAF": 0,
    "XOF": 0, "XPF": 0,
    # three minor units
    "BHD": 3, "IQD": 3, "JOD": 3, "KWD": 3, "OMR": 3, "TND": 3,
    # four minor units
    "CLF": 4,
    # two minor units
    "AUD": 2, "CAD": 2, "CHF": 2, "CNY": 2, "CZK": 2, "DKK": 2, "EUR": 2,
    "GBP": 2, "HKD": 2, "HUF": 2, "INR": 2, "MXN": 2, "NOK": 2, "NZD": 2,
    "PLN": 2, "RUB": 2, "SEK": 2, "SGD": 2, "TRY": 2, "USD": 2, "ZAR": 2,
}

# Symbols that name exactly one supported currency. "$" and "¥" are
# deliberately absent: they denote several supported currencies and must be
# resolved by an explicit code, never inferred.
CURRENCY_SYMBOLS: dict[str, str] = {
    "€": "EUR",
    "£": "GBP",
    "₹": "INR",
    "₺": "TRY",
    "₫": "VND",
    "zł": "PLN",
}

SUPPORTED_CURRENCIES: tuple[str, ...] = tuple(sorted(_FRACTION_DIGITS))


def normalize_currency(value: str) -> str:
    """Return the supported ISO code for an exact code or unambiguous symbol.

    Codes are case-insensitive; symbols must match exactly. Raises ValueError
    for empty, unknown or ambiguous values.
    """
    if not isinstance(value, str):
        raise ValueError("currency must be a string")
    text = value.strip()
    if not text:
        raise ValueError("currency is empty")
    code = text.upper()
    if code in _FRACTION_DIGITS:
        return code
    if text in CURRENCY_SYMBOLS:
        return CURRENCY_SYMBOLS[text]
    raise ValueError(
        f"unsupported currency {value!r}; supported: {', '.join(SUPPORTED_CURRENCIES)}")


def fraction_digits(code: str) -> int:
    """Minor-unit decimal places for a supported currency (0, 2, 3 or 4)."""
    return _FRACTION_DIGITS[normalize_currency(code)]
