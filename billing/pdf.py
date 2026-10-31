"""Server-side PDF for invoices and credit notes (ReportLab). No external service."""
from html import escape
from io import BytesIO
from pathlib import Path

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from billing.models import Invoice, Payment
from core.models import CompanySettings


_FONTS = Path(__file__).resolve().parent.parent / "static" / "fonts"
try:  # Noto Sans carries the rupee sign (U+20B9), which the built-in Helvetica lacks
    pdfmetrics.registerFont(TTFont("NS", str(_FONTS / "NotoSans-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("NS-B", str(_FONTS / "NotoSans-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("NS-I", str(_FONTS / "NotoSans-Italic.ttf")))
    pdfmetrics.registerFontFamily("NS", normal="NS", bold="NS-B", italic="NS-I", boldItalic="NS-B")
    REG, BOLD = "NS", "NS-B"
except Exception:  # missing font files: fall back to Helvetica and a text currency marker
    REG, BOLD = "Helvetica", "Helvetica-Bold"


def money(v):
    return f"{settings.CURRENCY_SYMBOL if REG == 'NS' else 'Rs. '}{v:,.2f}"


def _text(value):
    """Escape model text for ReportLab's XML-like Paragraph parser, preserving newlines."""
    return escape(str(value or ""), quote=False).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")


def render_invoice_pdf(inv):
    # Print output is a financial record; callers may hold an invoice instance from before
    # a payment/refund update, so resolve the persisted snapshot before rendering.
    inv = Invoice.objects.select_related("subscription").get(pk=inv.pk)
    co = CompanySettings.get()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=13 * mm, rightMargin=13 * mm,
                            topMargin=10 * mm, bottomMargin=11 * mm,
                            title=str(inv.number), author=str(co.name))
    base = ParagraphStyle("b", fontName=REG, fontSize=9, leading=11)
    muted = ParagraphStyle("m", parent=base, textColor=colors.HexColor("#6b7280"))
    h1 = ParagraphStyle("h1", parent=base, fontName=BOLD, fontSize=20, leading=23)
    right = ParagraphStyle("r", parent=base, alignment=2)
    kind = "Credit note" if inv.is_credit_note else "Invoice"
    story = []
    head = Table([[Paragraph(f"<b>{_text(co.name)}</b><br/><font color='#6b7280'>{_text(co.address)}"
                             f"{'<br/>Tax ID ' + _text(co.tax_id) if co.tax_id else ''}</font>", base),
                   Paragraph(f"<b>{kind}</b><br/>{_text(inv.number)}<br/><font color='#6b7280'>{_text(inv.get_state_display() if not inv.is_credit_note else '')}</font>",
                             ParagraphStyle("hh", parent=h1, alignment=2, fontSize=15, leading=18))]],
                 colWidths=[100 * mm, 74 * mm])
    head.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), REG), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.black), ("BOTTOMPADDING", (0, 0), (-1, 0), 6)]))
    story += [head, Spacer(1, 5 * mm)]
    meta = Table([[Paragraph(f"<font color='#6b7280'>Billed to</font><br/><b>{_text(inv.bill_to_name)}</b><br/>{_text(inv.bill_to_email)}<br/>"
                              f"{_text(inv.bill_to_address)}{'<br/>Tax ID ' + _text(inv.bill_to_tax_id) if inv.bill_to_tax_id else ''}", base),
                    Paragraph(f"<font color='#6b7280'>Issued</font> {inv.issue_date:%d %b %Y}<br/><font color='#6b7280'>Due</font> {inv.due_date:%d %b %Y}<br/>"
                              f"<font color='#6b7280'>Period</font> {inv.period_start:%d %b} – {inv.period_end:%d %b %Y}<br/><font color='#6b7280'>Subscription</font> {_text(inv.subscription.number)}", right)]],
                  colWidths=[100 * mm, 74 * mm])
    meta.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), REG), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [meta, Spacer(1, 5 * mm)]
    rows = [["Description", "Qty", "Unit price", "Discount", "Tax", "Amount"]]
    for line in inv.lines.all():
        rows.append([Paragraph(_text(line.description), base), str(line.quantity), money(line.unit_price),
                     money(line.discount) if line.discount else "—",
                     f"{line.tax_rate.normalize():f}%" if line.tax_rate else "—", money(line.amount)])
    table = Table(rows, colWidths=[72 * mm, 12 * mm, 24 * mm, 22 * mm, 16 * mm, 28 * mm], repeatRows=1)
    table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), REG), ("FONTNAME", (0, 0), (-1, 0), BOLD),
                               ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#6b7280")),
                               ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#d1d5db")),
                               ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#e5e7eb")),
                               ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [table, Spacer(1, 4 * mm)]
    totals = [["Subtotal", money(inv.subtotal)]]
    if inv.discount_total:
        totals.append([Paragraph(_text(inv.discount_label or "Discount"), base), "−" + money(inv.discount_total)])
    totals += [["Tax", money(inv.tax_total)], ["Total", money(inv.total)]]
    if not inv.is_credit_note:
        if inv.credited_total:
            totals.append(["Credited", "−" + money(inv.credited_total)])
        totals += [["Paid", money(inv.amount_paid)], ["Balance due", money(inv.balance)]]
    total_table = Table(totals, colWidths=[40 * mm, 34 * mm], hAlign="RIGHT")
    total_table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), REG), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                                     ("FONTSIZE", (0, 0), (-1, -1), 9), ("FONTNAME", (0, -1), (-1, -1), BOLD),
                                     ("LINEABOVE", (0, 3 if inv.discount_total else 2), (-1, 3 if inv.discount_total else 2), 0.8, colors.black),
                                     ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [total_table, Spacer(1, 4 * mm)]
    from core.numwords import rupees_in_words
    summary = inv.tax_summary
    if summary and inv.tax_total:
        story += [Paragraph("<b>Tax summary</b>", base), Spacer(1, 1 * mm)]
        tax_rows = [["Rate", "Taxable value", "Tax"]] + [
            [Paragraph(f"{_text(item['name'] or 'Tax')} {item['rate'].normalize():f}%", base), money(item["taxable"]), money(item["tax"])]
            for item in summary]
        tax_table = Table(tax_rows, colWidths=[50 * mm, 40 * mm, 34 * mm], hAlign="LEFT", repeatRows=1)
        tax_table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), REG), ("FONTSIZE", (0, 0), (-1, -1), 8),
                                       ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#6b7280")),
                                       ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#d1d5db")),
                                       ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                                       ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        story += [tax_table, Spacer(1, 2 * mm)]
    story += [Paragraph(f"<b>Amount in words:</b> <i>{_text(rupees_in_words(inv.total))}</i>", base), Spacer(1, 2 * mm)]
    if co.bank_details and not inv.is_credit_note:
        story += [Paragraph("<b>How to pay</b>", base), Paragraph(_text(co.bank_details), muted), Spacer(1, 2 * mm)]
    if co.invoice_terms:
        story += [Paragraph("<b>Terms and conditions</b>", base), Paragraph(_text(co.invoice_terms), muted), Spacer(1, 3 * mm)]

    payments = list(inv.payments.filter(status=Payment.SUCCEEDED).order_by("date", "pk"))
    if payments:
        story += [Paragraph("<b>Payment activity</b>", base), Spacer(1, 1 * mm)]
        payment_rows = [["Date", "Type", "Method", "Reference", "Amount"]]
        for payment in payments:
            payment_rows.append([payment.date.strftime("%d %b %Y"), "Refund" if payment.amount < 0 else "Payment",
                                 Paragraph(_text(payment.get_method_display()), base),
                                 Paragraph(_text(payment.reference or "—"), base), money(payment.amount)])
        payment_table = Table(payment_rows, colWidths=[25 * mm, 25 * mm, 38 * mm, 52 * mm, 24 * mm], repeatRows=1)
        payment_table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), REG), ("FONTNAME", (0, 0), (-1, 0), BOLD),
                                           ("FONTSIZE", (0, 0), (-1, -1), 8), ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#6b7280")),
                                           ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#d1d5db")),
                                           ("ALIGN", (-1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                           ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        story += [payment_table, Spacer(1, 2 * mm)]

    receipt_is_valid = (not inv.is_credit_note and inv.total > 0 and inv.state == Invoice.PAID
                        and inv.amount_paid >= inv.total and not inv.credited_total)
    left_signature = (Paragraph(f"<b>Received with thanks</b><br/>{_text(inv.bill_to_name)}", muted)
                      if receipt_is_valid else "")
    signature = Table([[left_signature, Paragraph(f"<b>For {_text(co.name)}</b><br/>Authorised signatory",
                                                   ParagraphStyle("sr", parent=muted, alignment=2))]],
                      colWidths=[85 * mm, 85 * mm])
    signature.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), REG), ("LINEABOVE", (0, 0), (0, 0), 0.6, colors.black),
                                   ("LINEABOVE", (1, 0), (1, 0), 0.6, colors.black), ("TOPPADDING", (0, 0), (-1, -1), 5)]))
    story += [Spacer(1, 4 * mm), signature, Spacer(1, 2 * mm),
              Paragraph(f"{_text(co.invoice_footer)} Computer-generated; valid without a physical signature. Doc ID {_text(inv.fingerprint)}.", muted)]
    doc.build(story)
    return buf.getvalue()
