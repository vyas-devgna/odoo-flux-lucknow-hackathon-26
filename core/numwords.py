"""Amounts in words, Indian numbering (lakh, crore), for invoices and quotations: "Rupees One Lakh Twenty Thousand Only"."""
from decimal import ROUND_HALF_UP, Decimal

ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_thousand(n):
    out = []
    if n >= 100:
        out.append(ONES[n // 100] + " Hundred")
        n %= 100
    if n >= 20:
        out.append(TENS[n // 10] + ((" " + ONES[n % 10]) if n % 10 else ""))
    elif n:
        out.append(ONES[n])
    return " ".join(out)


def _int_words(n):
    if n == 0:
        return "Zero"
    parts = []
    for div, name in ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand")):
        if n >= div:
            parts.append(_below_thousand(n // div) + " " + name)
            n %= div
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def rupees_in_words(amount):
    amount = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    neg = amount < 0
    amount = abs(amount)
    rupees, paise = int(amount), int((amount - int(amount)) * 100)
    text = "Rupees " + _int_words(rupees)
    if paise:
        text += " and " + _int_words(paise) + " Paise"
    return ("Minus " if neg else "") + text + " Only"
