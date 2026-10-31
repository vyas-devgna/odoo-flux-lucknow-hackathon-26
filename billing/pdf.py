"""Server-side PDF for invoices and credit notes (ReportLab). No external service."""
from io import BytesIO

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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


def render_invoice_pdf(inv):
    co = CompanySettings.get()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=inv.number, author=co.name)
    base = ParagraphStyle("b", fontName=REG, fontSize=9.5, leading=13)
    muted = ParagraphStyle("m", parent=base, textColor=colors.HexColor("#6b7280"))
    h1 = ParagraphStyle("h1", parent=base, fontName=BOLD, fontSize=22, leading=26)
    right = ParagraphStyle("r", parent=base, alignment=2)
    kind = "Credit note" if inv.is_credit_note else "Invoice"
    story = []
    head = Table([[Paragraph(f"<b>{co.name}</b><br/><font color='#6b7280'>{(co.address or '').replace(chr(10), '<br/>')}"
                             f"{'<br/>Tax ID ' + co.tax_id if co.tax_id else ''}</font>", base),
                   Paragraph(f"<b>{kind}</b><br/>{inv.number}<br/><font color='#6b7280'>{inv.get_state_display() if not inv.is_credit_note else ''}</font>", ParagraphStyle("hh", parent=h1, alignment=2, fontSize=16, leading=20))]],
                 colWidths=[100 * mm, 74 * mm])
    head.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), REG), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.black), ("BOTTOMPADDING", (0, 0), (-1, 0), 10)]))
    story += [head, Spacer(1, 8 * mm)]
    meta = Table([[Paragraph(f"<font color='#6b7280'>Billed to</font><br/><b>{inv.bill_to_name}</b><br/>{inv.bill_to_email}<br/>"
                             f"{(inv.bill_to_address or '').replace(chr(10), '<br/>')}{'<br/>Tax ID ' + inv.bill_to_tax_id if inv.bill_to_tax_id else ''}", base),
                   Paragraph(f"<font color='#6b7280'>Issued</font> {inv.issue_date:%d %b %Y}<br/><font color='#6b7280'>Due</font> {inv.due_date:%d %b %Y}<br/>"
                             f"<font color='#6b7280'>Period</font> {inv.period_start:%d %b} – {inv.period_end:%d %b %Y}<br/><font color='#6b7280'>Subscription</font> {inv.subscription.number}", right)]],
                 colWidths=[100 * mm, 74 * mm])
    meta.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), REG), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [meta, Spacer(1, 8 * mm)]
    rows = [["Description", "Qty", "Unit price", "Discount", "Tax", "Amount"]]
    for l in inv.lines.all():
        rows.append([Paragraph(l.description, base), str(l.quantity), money(l.unit_price), money(l.discount) if l.discount else "—",
                     f"{l.tax_rate.normalize():f}%" if l.tax_rate else "—", money(l.amount)])
    t = Table(rows, colWidths=[72 * mm, 12 * mm, 24 * mm, 22 * mm, 16 * mm, 28 * mm], repeatRows=1)
    t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), REG), ("FONTNAME", (0, 0), (-1, 0), BOLD), ("FONTSIZE", (0, 0), (-1, -1), 9), ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#6b7280")),
                           ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#d1d5db")), ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#e5e7eb")),
                           ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [t, Spacer(1, 6 * mm)]
    tot = [["Subtotal", money(inv.subtotal)]]
    if inv.discount_total:
        tot.append([inv.discount_label or "Discount", "−" + money(inv.discount_total)])
    tot.append(["Tax", money(inv.tax_total)])
    tot.append(["Total", money(inv.total)])
    if not inv.is_credit_note:
        if inv.credited_total:
            tot.append(["Credited", "−" + money(inv.credited_total)])
        tot += [["Paid", money(inv.amount_paid)], ["Balance due", money(inv.balance)]]
    tt = Table(tot, colWidths=[40 * mm, 34 * mm], hAlign="RIGHT")
    tt.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), REG), ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 9.5), ("FONTNAME", (0, -1), (-1, -1), BOLD),
                            ("LINEABOVE", (0, 3 if inv.discount_total else 2), (-1, 3 if inv.discount_total else 2), 0.8, colors.black)]))
    story += [tt, Spacer(1, 10 * mm)]
    from core.numwords import rupees_in_words
    summary = inv.tax_summary
    if summary and inv.tax_total:
        story += [Paragraph("<b>Tax summary</b>", base), Spacer(1, 2 * mm)]
        ts = Table([["Rate", "Taxable value", "Tax"]] + [[f"{t['name'] or 'Tax'} {t['rate'].normalize():f}%", money(t["taxable"]), money(t["tax"])] for t in summary], colWidths=[50 * mm, 40 * mm, 34 * mm], hAlign="LEFT")
        ts.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), REG), ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#6b7280")), ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#d1d5db")),
                                ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story += [ts, Spacer(1, 4 * mm)]
    story += [Paragraph(f"<b>Amount in words:</b> <i>{rupees_in_words(inv.total)}</i>", base), Spacer(1, 4 * mm)]
    if co.bank_details and not inv.is_credit_note:
        story += [Paragraph("<b>How to pay</b>", base), Paragraph(co.bank_details.replace("\n", "<br/>"), muted), Spacer(1, 3 * mm)]
    if co.invoice_terms:
        story += [Paragraph("<b>Terms and conditions</b>", base), Paragraph(co.invoice_terms.replace("\n", "<br/>"), muted), Spacer(1, 6 * mm)]
    sig = Table([[Paragraph(f"<b>Received with thanks</b><br/>{inv.bill_to_name}", muted), Paragraph(f"<b>For {co.name}</b><br/>Authorised signatory", ParagraphStyle("sr", parent=muted, alignment=2))]], colWidths=[85 * mm, 85 * mm])
    sig.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), REG), ("LINEABOVE", (0, 0), (0, 0), 0.6, colors.black), ("LINEABOVE", (1, 0), (1, 0), 0.6, colors.black), ("TOPPADDING", (0, 0), (-1, -1), 6)]))
    story += [Spacer(1, 10 * mm), sig, Spacer(1, 4 * mm), Paragraph(f"{co.invoice_footer} Computer-generated; valid without a physical signature. Doc ID {inv.fingerprint}.", muted)]
    doc.build(story)
    return buf.getvalue()
