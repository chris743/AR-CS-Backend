"""Generate a PDF invoice matching the Cobblestone Fruit layout (see 106401 (1).pdf).

Main entry point: ``generate_invoice(lines, ...)`` -> writes a PDF and returns its path.
"""

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

# --- page geometry (US Letter, points) -------------------------------------
PAGE_W, PAGE_H = letter  # 612 x 792

# Fonts (the original embeds three TrueType faces; these are the std equivalents)
F_REG = "Helvetica"
F_BOLD = "Helvetica-Bold"
F_ITAL = "Helvetica-Oblique"
SIZE = 9.9
SIZE_SM = 8.0

# Column reference edges (taken straight from the source PDF)
DESC_X = 18.7          # description, left aligned
QTY_R = 423.7          # quantity, right aligned
UOM_C = 440.5          # uom, centered
PRICE_R = 515.2        # price, right aligned
AMT_R = 577.4          # amount, right aligned

MARGIN_L = 18.6
MARGIN_R = 582.0

ROW_H = 13.45          # vertical step between line items
TABLE_TOP = 352.6      # y (top-down) of the line under the column headers
FOOTER_LINE_Y = 722.3  # legal/footer area begins here
MAX_ROW_BOTTOM = 700   # last y a row may occupy before spilling to a new page

# PACA boilerplate printed at the bottom of every invoice.
PACA_TEXT = [
    "The perishable agricultural commodities listed on this invoice are sold subject to the statutory trust authorized by section 5(c) of the Perishable Agricultural",
    "Commodities Act, 1930 (7 U.S.C. 499e(c)). The seller of these commodities retains a trust claim over these commodities, all inventories of food or other",
    "products derived from these commodities, and any receivables or proceeds from the sale of these commodities until full payment is received.",
]


def _money(v):
    """Format a number with thousands separators and two decimals."""
    return f"{v:,.2f}"


def _qty(v):
    """Quantities print as plain integers when whole, else with decimals."""
    if isinstance(v, (int,)) or (isinstance(v, float) and v.is_integer()):
        return f"{int(v)}"
    return f"{v:g}"


class _Canvas:
    """Thin wrapper so we can draw using the source PDF's top-down coordinates."""

    def __init__(self, c):
        self.c = c

    def text(self, x, top, s, font=F_REG, size=SIZE):
        self.c.setFont(font, size)
        # source y values are glyph-box tops; baseline sits ~0.8*size below
        self.c.drawString(x, PAGE_H - top - size * 0.8, str(s))

    def rtext(self, x_right, top, s, font=F_REG, size=SIZE):
        self.c.setFont(font, size)
        self.c.drawRightString(x_right, PAGE_H - top - size * 0.8, str(s))

    def ctext(self, x_center, top, s, font=F_REG, size=SIZE):
        self.c.setFont(font, size)
        self.c.drawCentredString(x_center, PAGE_H - top - size * 0.8, str(s))

    def line(self, x0, top0, x1, top1, width=0.72, gray=0.0):
        self.c.setLineWidth(width)
        self.c.setStrokeColorRGB(gray, gray, gray)
        self.c.line(x0, PAGE_H - top0, x1, PAGE_H - top1)

    def box(self, x0, top0, x1, top1, width=0.72, gray=0.0):
        self.c.setLineWidth(width)
        self.c.setStrokeColorRGB(gray, gray, gray)
        self.c.rect(x0, PAGE_H - top1, x1 - x0, top1 - top0, stroke=1, fill=0)


def generate_invoice(
    lines,
    output_path="invoice.pdf",
    *,
    company_name="COBBLESTONE FRUIT",
    company_address=("730 N OLIVER AVE", "SANGER CA 93657"),
    invoice_number="106401",
    invoice_date="May 01, 2026",
    ship_date="May 01, 2026",
    delivery_date="Mar 15, 2026",
    pay_terms="Net 30",
    sold_to=("CREEKSIDE ORGANICS, INC.", "1201 24TH STREET STE B110-146", "BAKERSFIELD CA 93301"),
    ship_to=("CREEKSIDE ORGANICS, INC.", "1201 24TH STREET STE B110-146", "BAKERSFIELD CA 93301"),
    sale_terms="Net 30",
    order="Mar 15, 2026",
    cust_po="PACKING WE 031526",
    salesperson="HOUSE ACCOUNT",
    via="",
    currency="USD",
    carrier="",
    trailer_lic="",
    state="",
    broker="",
):
    """Render a PDF invoice.

    ``lines`` is a list of dicts, one per line item, with keys:
        description (str), quantity (number), uom (str, optional),
        price (number), amount (number, optional -> quantity*price).

    Returns the output path.
    """
    # normalize line items and compute the grand total
    items = []
    total = 0.0
    for ln in lines:
        qty = ln.get("quantity", 0) or 0
        price = ln.get("price", 0) or 0
        amount = ln.get("amount")
        if amount is None:
            amount = qty * price
        total += amount
        items.append({
            "description": ln.get("description", ""),
            "quantity": qty,
            "uom": ln.get("uom", ""),
            "price": price,
            "amount": amount,
        })

    # paginate
    rows_first = int((MAX_ROW_BOTTOM - (TABLE_TOP + ROW_H)) / ROW_H)
    pages = [items[i:i + rows_first] for i in range(0, len(items), rows_first)] or [[]]
    n_pages = len(pages)

    c = canvas.Canvas(output_path, pagesize=letter)
    meta = dict(
        company_name=company_name, company_address=company_address,
        invoice_number=invoice_number, invoice_date=invoice_date,
        ship_date=ship_date, delivery_date=delivery_date, pay_terms=pay_terms,
        sold_to=sold_to, ship_to=ship_to, sale_terms=sale_terms, order=order,
        cust_po=cust_po, salesperson=salesperson, via=via, currency=currency,
        carrier=carrier, trailer_lic=trailer_lic, state=state, broker=broker,
    )

    for pi, page_items in enumerate(pages):
        last = pi == n_pages - 1
        _draw_page(_Canvas(c), meta, page_items, pi + 1, n_pages,
                   total if last else None)
        c.showPage()

    c.save()
    return output_path


def _draw_page(p, m, items, page_no, n_pages, total):
    # ---- header: centred company block --------------------------------------
    p.ctext(303, 18.6, m["company_name"], F_REG, SIZE)
    for i, addr in enumerate(m["company_address"]):
        p.ctext(303, 29.6 + i * 11, addr, F_REG, SIZE)

    p.ctext(306, 93.9, "INVOICE", F_BOLD, 18.0)

    # ---- right-hand invoice details ----------------------------------------
    labels = [
        ("Invoice #:", m["invoice_number"], 116.0),
        ("Invoice:", m["invoice_date"], 130.3),
        ("Ship:", m["ship_date"], 144.5),
        ("Delivery:", m["delivery_date"], 158.8),
        ("Pay Terms:", m["pay_terms"], 173.0),
    ]
    for label, value, y in labels:
        p.text(451.4, y, label, F_BOLD, SIZE)
        p.text(500.0, y, value, F_REG, SIZE)

    p.text(525.8, 269.3, f"Page {page_no} of {n_pages}", F_REG, SIZE)

    # ---- Sold To / Ship To --------------------------------------------------
    p.text(18.7, 190.3, "Sold To:", F_BOLD, SIZE)
    p.text(291.0, 190.3, "Ship To:", F_BOLD, SIZE)
    for i, addr in enumerate(m["sold_to"]):
        p.text(62.2, 189.7 + i * 11.05, addr, F_REG, SIZE)
    for i, addr in enumerate(m["ship_to"]):
        p.text(334.4, 189.7 + i * 11.05, addr, F_REG, SIZE)

    # ---- terms / shipping info box ------------------------------------------
    p.box(18.6, 285.7, 582.0, 331.7, width=0.9)
    # subtle drop shadow on the right & bottom edges
    p.line(582.0, 288.6, 584.9, 331.7, width=2.9, gray=0.627)
    p.line(21.5, 333.1, 584.9, 333.1, width=2.9, gray=0.627)

    info_left = [
        ("Sale Terms:", m["sale_terms"], 83.2, 289.2),
        ("Order:", m["order"], 54.7, 303.4),
        ("Cust PO:", m["cust_po"], 64.4, 317.6),
    ]
    for label, value, vx, y in info_left:
        p.text(20.2, y, label, F_BOLD, SIZE)
        if value:
            p.text(vx, y, value, F_REG, SIZE)

    info_mid = [
        ("Salesperson:", m["salesperson"], 278.9, 289.2),
        ("Via:", m["via"], 240.0, 303.4),
        ("Currency:", m["currency"], 264.0, 317.6),
    ]
    for label, value, vx, y in info_mid:
        p.text(213.7, y, label, F_BOLD, SIZE)
        if value:
            p.text(vx, y, value, F_REG, SIZE)

    p.text(382.4, 289.2, "Carrier:", F_BOLD, SIZE)
    if m["carrier"]:
        p.text(420.0, 289.2, m["carrier"], F_REG, SIZE)
    p.text(382.4, 303.4, "Trailer lic:", F_BOLD, SIZE)
    if m["trailer_lic"]:
        p.text(425.0, 303.4, m["trailer_lic"], F_REG, SIZE)
    p.text(506.2, 303.4, "St:", F_BOLD, SIZE)
    if m["state"]:
        p.text(520.0, 303.4, m["state"], F_REG, SIZE)
    p.text(382.4, 317.6, "Broker:", F_BOLD, SIZE)
    if m["broker"]:
        p.text(420.0, 317.6, m["broker"], F_REG, SIZE)

    # ---- column headers -----------------------------------------------------
    p.ctext((DESC_X + 363.7) / 2, 341.0, "Description", F_BOLD, SIZE)
    p.text(376.4, 341.0, "Quantity", F_BOLD, SIZE)
    p.text(428.8, 341.0, "UOM", F_BOLD, SIZE)
    p.text(474.1, 341.0, "Price", F_BOLD, SIZE)
    p.text(529.8, 341.0, "Amount", F_BOLD, SIZE)
    # underlines beneath each header segment
    for x0, x1 in [(18.7, 363.7), (369.7, 423.7), (428.2, 452.9),
                   (457.4, 515.2), (519.7, 577.4)]:
        p.line(x0, TABLE_TOP, x1, TABLE_TOP, width=0.72)

    # ---- line items ---------------------------------------------------------
    y = TABLE_TOP + 2.0
    last_y = y
    for it in items:
        p.text(DESC_X, y, it["description"], F_REG, SIZE)
        p.rtext(QTY_R, y, _qty(it["quantity"]), F_REG, SIZE)
        if it["uom"]:
            p.ctext(UOM_C, y, it["uom"], F_REG, SIZE)
        p.rtext(PRICE_R, y, _money(it["price"]), F_REG, SIZE)
        p.rtext(AMT_R, y, _money(it["amount"]), F_REG, SIZE)
        last_y = y
        y += ROW_H

    # ---- invoice total (last page only) -------------------------------------
    if total is not None:
        total_y = last_y + ROW_H + 2.5
        # short rules above the quantity and amount columns
        p.line(369.7, total_y - 2.9, 423.7, total_y - 2.9, width=0.72)
        p.line(519.7, total_y - 2.9, 577.4, total_y - 2.9, width=0.72)
        p.text(25.4, total_y, "INVOICE TOTAL:", F_REG, SIZE)
        p.rtext(AMT_R, total_y, _money(total), F_REG, SIZE)

    # ---- footer -------------------------------------------------------------
    p.line(MARGIN_L, FOOTER_LINE_Y, 591.7, FOOTER_LINE_Y, width=0.72)
    p.ctext(305, 725.8, "Please return a copy of this invoice with your remittance - Thank You",
            F_ITAL, SIZE)
    for i, ln in enumerate(PACA_TEXT):
        p.ctext(312, 738.7 + i * 11.2, ln, F_REG, SIZE_SM)
    p.line(MARGIN_L, 773.3, 591.7, 773.3, width=0.72)


if __name__ == "__main__":
    # demo with the data from the sample invoice
    sample = [
        {"description": "PACKING SERVICES - COV MANDARIN 10/3 LB BAG", "quantity": 1577, "price": 11.96},
        {"description": "PACKING SERVICES - COV MANDARIN 6/5 LB BAG", "quantity": 300, "price": 10.90},
        {"description": "PACKING SERVICES - COV MANDARIN 25 LB CARTON", "quantity": 60, "price": 7.50},
        {"description": "PACKING SERVICES - ORG MANDARIN 15/2 LB BAG", "quantity": 1452, "price": 12.81},
        {"description": "PACKING SERVICES - ORG MANDARIN 25 LB CARTON", "quantity": 1341, "price": 7.90},
        {"description": "PACKING SERVICES - ORG ORANGE CARA 40# FILL", "quantity": 1369, "price": 8.50},
        {"description": "PACKING SERVICES - ORG ORANGE NAVEL 10/4 LB BAG", "quantity": 10, "price": 12.50},
        {"description": "PACKING SERVICES - ORG ORANGE 12/3 LB BAG", "quantity": 126, "price": 13.00},
        {"description": "PACKING SERVICES - ORG ORANGE 12/2 LB BAG", "quantity": 72, "price": 11.49},
        {"description": "PACKING SERVICES - ORG MANDARIN 12/2 LB BAG", "quantity": 120, "price": 12.26},
        {"description": "PACKING SERVICES - ORG LEMON CARTON", "quantity": 11, "price": 9.01},
        {"description": "PACKING SERVICES - ORG MANDARIN 8/3 LB BAG", "quantity": 300, "price": 9.00},
        {"description": "PACKING SERVICES - ORG MANDARIN CARTON", "quantity": 320, "price": 10.51},
        {"description": "PACKING SERVICES - ORG LEMON 18/2 LB BAG", "quantity": 4, "price": 12.76},
        {"description": "PACKING SERVICES - ORG MANDARIN 10/3 LB BAG", "quantity": 1980, "price": 12.36},
    ]
    path = generate_invoice(sample, "invoice.pdf")
    print("wrote", path)
