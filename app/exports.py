"""CSV / Excel / PDF renderers for reports, and the GST invoice PDF."""
import csv
import io
import re

from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .config import FACTORY
from .money import amount_words, paise_str

NUM = re.compile(r"^-?\d+(\.\d+)?$")


def to_csv(rep) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([rep["title"]])
    for s in rep["sections"]:
        w.writerow([])
        if s["name"]:
            w.writerow([s["name"]])
        w.writerow(s["headers"])
        w.writerows(s["rows"])
    return out.getvalue().encode("utf-8-sig")  # BOM so Excel opens it with the right encoding


def to_xlsx(rep) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for n, s in enumerate(rep["sections"], 1):
        ws = wb.create_sheet(((s["name"] or rep["title"])[:25] or f"Sheet{n}").replace("/", "-").replace(":", "-").replace("(", "").replace(")", "") + (f" {n}" if n > 1 and not s["name"] else ""))
        ws.append(s["headers"])
        for r in s["rows"]:
            ws.append([float(c) if isinstance(c, str) and NUM.match(c) and h not in ("HSN", "Recipient GSTIN") and len(c) < 12 else c
                       for c, h in zip(r, s["headers"])])
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(40, max(len(str(c.value or "")) for c in col) + 2)
    if not wb.sheetnames:
        wb.create_sheet("Empty")
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


def to_pdf(rep, subtitle="") -> bytes:
    buf, st = io.BytesIO(), getSampleStyleSheet()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm)
    story = [Paragraph(f"{FACTORY['name']}: {rep['title']}", st["Title"]), Paragraph(subtitle, st["Normal"]), Spacer(1, 6)]
    small = st["BodyText"].clone("small", fontSize=7, leading=8.5)
    for s in rep["sections"]:
        if s["name"]:
            story.append(Paragraph(s["name"], st["Heading4"]))
        data = [[Paragraph(f"<b>{h}</b>", small) for h in s["headers"]]] + [[Paragraph(str(c), small) for c in r] for r in s["rows"]]
        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfe9e2")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [t, Spacer(1, 8)]
    doc.build(story)
    return buf.getvalue()


def invoice_pdf(inv) -> bytes:
    """Tax invoice. (Built-in PDF fonts lack the rupee glyph, so amounts are in Rs.)"""
    buf, st = io.BytesIO(), getSampleStyleSheet()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm)
    small = st["BodyText"].clone("s", fontSize=8, leading=10)
    c = inv.customer
    title = "TAX INVOICE" + (" (CANCELLED)" if inv.status == "CANCELLED" else "")
    story = [Paragraph(title, st["Title"]),
             Table([[Paragraph(f"<b>{FACTORY['name']}</b><br/>{FACTORY['address']}<br/>GSTIN: {FACTORY['gstin']}<br/>State: {FACTORY['state']}", small),
                     Paragraph(f"<b>Invoice no:</b> {inv.inv_no}<br/><b>Date:</b> {inv.date:%d-%m-%Y}<br/><b>Order:</b> {inv.order.order_no}<br/><b>Place of supply:</b> {inv.place_of_supply}", small)],
                    [Paragraph(f"<b>Bill to</b><br/>{c.name}<br/>{c.address or ''}<br/>GSTIN: {c.gstin or 'Unregistered'}<br/>State: {c.state}", small), ""]],
                   colWidths=[95 * mm, 87 * mm], style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.5, colors.grey), ("LINEBELOW", (0, 0), (-1, 0), 0.25, colors.grey)]),
             Spacer(1, 8)]
    head = ["#", "Description", "HSN", "Qty", "Rate", "Taxable", "GST %", "CGST", "SGST", "IGST", "Amount"]
    rows = [head]
    for n, l in enumerate(inv.lines, 1):
        tax = l.cgst_paise + l.sgst_paise + l.igst_paise
        rows.append([n, l.description, l.hsn or "", l.qty, paise_str(l.rate_paise), paise_str(l.taxable_paise), f"{l.gst_bp / 100:g}",
                     paise_str(l.cgst_paise), paise_str(l.sgst_paise), paise_str(l.igst_paise), paise_str(l.taxable_paise + tax)])
    rows.append(["", "Total", "", sum(l.qty for l in inv.lines), "", paise_str(inv.taxable_paise), "", paise_str(inv.cgst_paise), paise_str(inv.sgst_paise), paise_str(inv.igst_paise), paise_str(inv.total_paise)])
    t = Table(rows, repeatRows=1, colWidths=[7 * mm, 44 * mm, 14 * mm, 10 * mm, 15 * mm, 20 * mm, 11 * mm, 16 * mm, 16 * mm, 16 * mm, 20 * mm])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7.5), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfe9e2")),
                           ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("ALIGN", (3, 1), (-1, -1), "RIGHT")]))
    story += [t, Spacer(1, 8), Paragraph(f"<b>Amount in words:</b> {amount_words(inv.total_paise)}", small), Spacer(1, 4),
              Paragraph("Tax is charged as CGST + SGST for supplies within Tamil Nadu and as IGST for supplies to other states. "
                        "This is a computer-generated invoice for a demo business.", small)]
    doc.build(story)
    return buf.getvalue()
