"""CSV exports are opened in spreadsheets, where a cell starting with = + - @ is executed as a formula.
Prefix such text so customer-controlled values (names, notes) can't become formulas."""
from decimal import Decimal

_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def safe(value):
    if isinstance(value, (int, float, Decimal)) or value is None:
        return value
    text = str(value)
    return "'" + text if text.startswith(_TRIGGERS) else text


def row(values):
    return [safe(v) for v in values]
