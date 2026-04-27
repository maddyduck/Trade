"""Invoice PDF generation.

Pure-Python rendering using ReportLab — no external Chromium needed (which
matters for keeping the Render plan small). Output is an A4 PDF bytes blob
suitable for emailing as an attachment or serving via HTTP.

The visual design intentionally matches the Sorted look — Bricolage display
font fallbacks, ink/cream palette, plenty of whitespace.
"""
from __future__ import annotations

from datetime import date
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models import Invoice

# Colours that mirror the design system in app.css
INK = colors.HexColor("#1C1A17")
INK_SOFT = colors.HexColor("#5C5852")
CREAM = colors.HexColor("#FAF7F1")
AMBER = colors.HexColor("#D97706")
RULE = colors.HexColor("#E8E2D8")


def _money(pence: int, currency: str = "GBP") -> str:
    sym = {"GBP": "£", "EUR": "€", "USD": "$"}.get(currency, currency + " ")
    if pence % 100 == 0:
        return f"{sym}{pence // 100:,}"
    return f"{sym}{pence / 100:,.2f}"


def render_invoice_pdf(invoice: Invoice) -> bytes:
    """Render an invoice as a PDF byte string."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=f"Invoice {invoice.number}",
        author=invoice.trade.business_name,
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle(
        "h1", parent=styles["Heading1"],
        fontSize=22, leading=26, textColor=INK, spaceAfter=12,
    )
    body = ParagraphStyle(
        "body", parent=styles["BodyText"],
        fontSize=10, leading=14, textColor=INK,
    )
    body_soft = ParagraphStyle(
        "body_soft", parent=body, textColor=INK_SOFT,
    )
    body_right = ParagraphStyle(
        "body_right", parent=body, alignment=TA_RIGHT,
    )
    label = ParagraphStyle(
        "label", parent=body_soft,
        fontSize=8, leading=10, textColor=INK_SOFT,
        textTransform="uppercase",
    )

    booking = invoice.booking
    trade = invoice.trade

    elems = []

    # Header — invoice number + status banner
    header_data = [
        [
            Paragraph(f"<b>INVOICE</b><br/><font size='9' color='#5C5852'>{invoice.number}</font>", h1),
            Paragraph(
                f"<para alignment='right'>"
                f"<b>{trade.business_name}</b><br/>"
                f"<font color='#5C5852'>{trade.contact_name}</font><br/>"
                f"<font color='#5C5852'>{trade.email}</font><br/>"
                f"<font color='#5C5852'>{trade.phone}</font>"
                f"</para>",
                body,
            ),
        ]
    ]
    header = Table(header_data, colWidths=[None, 80 * mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elems.append(header)
    elems.append(Spacer(1, 12))

    # Customer / job block
    issued = invoice.issued_at.date() if invoice.issued_at else date.today()
    due = invoice.due_date or date.today()

    cust_data = [
        [
            Paragraph(
                "<b><font size='8' color='#5C5852'>BILL TO</font></b><br/>"
                f"<font size='10'><b>{booking.customer_name}</b></font><br/>"
                f"<font size='10' color='#5C5852'>{booking.customer_email}</font><br/>"
                f"<font size='10' color='#5C5852'>{booking.customer_postcode}</font>",
                body,
            ),
            Paragraph(
                f"<para alignment='right'>"
                f"<b><font size='8' color='#5C5852'>BOOKING REF</font></b><br/>"
                f"<font size='10'>{booking.reference}</font><br/>"
                f"<br/>"
                f"<b><font size='8' color='#5C5852'>ISSUED</font></b> "
                f"<font size='10'>{issued.strftime('%d %b %Y')}</font><br/>"
                f"<b><font size='8' color='#5C5852'>DUE</font></b> "
                f"<font size='10'>{due.strftime('%d %b %Y')}</font>"
                f"</para>",
                body,
            ),
        ]
    ]
    cust = Table(cust_data, colWidths=[None, 80 * mm])
    cust.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
    ]))
    elems.append(cust)

    # Line items table
    items_header = [
        Paragraph("<b>Description</b>", body),
        Paragraph("<para alignment='right'><b>Qty</b></para>", body),
        Paragraph("<para alignment='right'><b>Unit price</b></para>", body),
        Paragraph("<para alignment='right'><b>Amount</b></para>", body),
    ]
    rows = [items_header]

    for item in invoice.line_items:
        qty = item.quantity_milli / 1000
        qty_str = f"{qty:.2f}".rstrip("0").rstrip(".") if qty != int(qty) else str(int(qty))
        rows.append([
            Paragraph(item.description, body),
            Paragraph(f"<para alignment='right'>{qty_str}</para>", body),
            Paragraph(f"<para alignment='right'>{_money(item.unit_price_pence, invoice.currency)}</para>", body),
            Paragraph(f"<para alignment='right'>{_money(item.total_pence, invoice.currency)}</para>", body),
        ])

    items_tbl = Table(rows, colWidths=[None, 18 * mm, 28 * mm, 28 * mm])
    items_tbl.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 1, INK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elems.append(items_tbl)
    elems.append(Spacer(1, 12))

    # Totals
    totals_rows = []
    totals_rows.append([
        Paragraph("<font color='#5C5852'>Subtotal</font>", body_right),
        Paragraph(_money(invoice.subtotal_pence, invoice.currency), body_right),
    ])
    if invoice.is_vat_registered and invoice.vat_pence > 0:
        rate_pct = invoice.vat_rate_bps / 100
        totals_rows.append([
            Paragraph(f"<font color='#5C5852'>VAT ({rate_pct:.0f}%)</font>", body_right),
            Paragraph(_money(invoice.vat_pence, invoice.currency), body_right),
        ])
    totals_rows.append([
        Paragraph("<b>Total</b>", body_right),
        Paragraph(f"<b>{_money(invoice.total_pence, invoice.currency)}</b>", body_right),
    ])
    if invoice.deposit_pence > 0:
        totals_rows.append([
            Paragraph("<font color='#047857'>Deposit paid</font>", body_right),
            Paragraph(f"<font color='#047857'>−{_money(invoice.deposit_pence, invoice.currency)}</font>", body_right),
        ])
    if invoice.amount_paid_pence > 0:
        totals_rows.append([
            Paragraph("<font color='#047857'>Already paid</font>", body_right),
            Paragraph(f"<font color='#047857'>−{_money(invoice.amount_paid_pence, invoice.currency)}</font>", body_right),
        ])
    totals_rows.append([
        Paragraph("<b>Amount due</b>", body_right),
        Paragraph(
            f"<b><font size='14' color='#D97706'>{_money(invoice.amount_due_pence, invoice.currency)}</font></b>",
            body_right,
        ),
    ])

    totals_tbl = Table(totals_rows, colWidths=[None, 40 * mm], hAlign="RIGHT")
    totals_tbl.setStyle(TableStyle([
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEABOVE", (0, -1), (-1, -1), 1, INK),
        ("BACKGROUND", (0, -1), (-1, -1), CREAM),
    ]))
    elems.append(totals_tbl)
    elems.append(Spacer(1, 16))

    # Notes
    if invoice.notes:
        elems.append(Paragraph("<b>Notes</b>", label))
        elems.append(Paragraph(invoice.notes.replace("\n", "<br/>"), body))
        elems.append(Spacer(1, 8))

    # Payment instruction
    if invoice.stripe_payment_link_url and invoice.amount_due_pence > 0:
        elems.append(Paragraph(
            f"<b>Pay online:</b> <font color='#D97706'>{invoice.stripe_payment_link_url}</font>",
            body,
        ))
        elems.append(Spacer(1, 4))

    # Footer
    if trade.credentials:
        elems.append(Spacer(1, 12))
        elems.append(Paragraph(
            f"<font size='8' color='#5C5852'>{trade.credentials}</font>", body,
        ))
    if invoice.is_vat_registered and invoice.vat_number:
        elems.append(Paragraph(
            f"<font size='8' color='#5C5852'>VAT registration: {invoice.vat_number}</font>",
            body,
        ))

    doc.build(elems)
    return buf.getvalue()
