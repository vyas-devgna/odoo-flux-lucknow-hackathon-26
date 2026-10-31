from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def q(value):
    """Round to paise, half up. Every stored money amount goes through this."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)
