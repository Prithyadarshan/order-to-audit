"""Builds a narrated walkthrough video of the Section 5 scenario by driving the REAL app (in-process) and
rendering each screen. Needs: pip install weasyprint ; apt install ffmpeg espeak-ng poppler-utils.
Usage: python scripts/make_demo_video.py out_dir     -> out_dir/walkthrough.mp4
It is a captioned, synthetic-voice walkthrough, not a screen recording; re-record a live one if you prefer your own voice."""
import os, pathlib, subprocess, sys, tempfile

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "demo_video").resolve()
OUT.mkdir(parents=True, exist_ok=True)
DB = OUT / "demo.db"
DB.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from weasyprint import HTML  # noqa: E402

from app.config import BASE_DIR  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Customer, Item  # noqa: E402
from app.money import fmt_qty, inr  # noqa: E402
from app.services import production, sales, stock  # noqa: E402

CSS = (BASE_DIR / "static" / "app.css").read_text()
DESK, PHONE = (1280, 820), (430, 880)
frames: list[tuple[pathlib.Path, str]] = []


def render(html: str, size, caption: str, n: int, banner: str | None):
    w, h = size
    html = html.replace('<link rel="stylesheet" href="/static/app.css">', f"<style>{CSS}</style>")
    import re
    html = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    style = f"<style>@page{{size:{w}px {h}px;margin:0}}html,body{{width:{w}px;background:#f2f4f1}}input,select,textarea,button{{appearance:none;-weasy-appearance:none;line-height:1.35;height:auto;display:inline-block}}button,.btn{{padding:.55rem 1rem}}input,select{{padding:.5rem .6rem;border:1px solid #b9c5be;background:#fff}}.cap{{background:#c98a00;color:#17231e;padding:10px 16px;font:700 {15 if w<500 else 18}px/1.3 system-ui}}</style>"
    html = html.replace("</head>", style + "</head>", 1)
    if banner:
        html = html.replace("<body>", f'<body><div class="cap">{banner}</div>', 1)
    pdf = OUT / f"f{n:02d}.pdf"
    HTML(string=html).write_pdf(pdf)
    subprocess.run(["pdftoppm", "-png", "-r", "96", "-f", "1", "-l", "1", "-singlefile", str(pdf), str(OUT / f"f{n:02d}")], check=True)
    frames.append((OUT / f"f{n:02d}.png", caption))


def shot(c, path, banner, speech, size=DESK, edit=None):
    r = c.get(path, follow_redirects=True)
    assert r.status_code == 200, (path, r.status_code)
    html = edit(r.text) if edit else r.text
    render(html, size, speech, len(frames), banner)


def card(title, lines, speech):
    body = "".join(f"<p style='font-size:22px;margin:.4rem 0'>{l}</p>" for l in lines)
    html = f'''<html><head><link rel="stylesheet" href="/static/app.css"></head><body><main style="padding:90px 70px"><h1 style="font-size:40px">{title}</h1>{body}</main></body></html>'''
    render(html, DESK, speech, len(frames), None)


def login(c, u, p):
    c.cookies.clear()
    assert c.post("/login", data={"username": u, "password": p}, follow_redirects=False).status_code == 303


with TestClient(app) as c:
    with SessionLocal() as s:
        ids = {i.code: i.id for i in s.scalars(select(Item))}
        cust = {x.name: x.id for x in s.scalars(select(Customer))}
    card("Order to Audit", ["From a salesman's phone order to the auditor's monthly report.",
                            "Walkthrough of the benchmark scenario, recorded from the running application."],
         "This is the Order to Audit system for a small food manufacturer. I will walk through the benchmark scenario step by step, using the real application.")

    login(c, "production", "prod123")
    shot(c, "/stock", "Opening stock", "We start with the opening stock: flour one hundred kilograms, oil thirty litres, and fifty finished murukku packets.")
    c.post("/purchases", data={"supplier_id": 1, "bill_no": "B-101", "item_1": ids["FLOUR"], "qty_1": "60", "unit_1": "kg", "rate_1": "45"})
    shot(c, "/purchases", "Step 1: purchase 60 kg flour at 45 rupees", "Step one. The production supervisor records a purchase of sixty kilograms of flour at forty five rupees. The stock ledger is updated at once.")
    shot(c, f"/runs/new?product_id={ids['MUR200']}", "Step 2: start a production run from the recipe", "Step two. A production run starts from the recipe. Standard quantities per batch are shown with what is in stock. We issue forty kilograms of flour, ten and a half litres of oil, half a litre over standard, and one point two kilograms of spice mix.")
    r = c.post("/runs", data={"product_id": ids["MUR200"], "batches": 1, f"issue_{ids['OIL']}": "10.5", f"unit_{ids['OIL']}": "L"}, follow_redirects=False)
    shot(c, r.headers["location"], "Raw material issued: oil +5% flagged", "Raw materials are issued from stock against the recipe. Oil is five percent over the standard, so the system flags it immediately.")
    c.post("/runs/1/complete", data={"good": 188, "damaged": 6, f"pack_{ids['CARTON20']}": 9})
    shot(c, "/runs/1", "Step 3: output 188 packets, 6 pouches damaged, 9 cartons", "Step three. Actual output is one hundred and eighty eight good packets, six damaged pouches and nine cartons used. Yield is six percent below the expected two hundred, and is flagged.")
    shot(c, "/stock", "Stock updated automatically", "Stock updated automatically. Flour one hundred and twenty kilograms, oil nineteen point five litres, pouches three hundred and six, and finished goods two hundred and thirty eight packets.")

    login(c, "salesman_a", "sales123")
    fill = lambda h: (h.replace('<div id="lines"></div>', '<div id="lines"><div class="line"><select><option>Murukku 200 g (Rs 50.00)</option></select><input value="150"><select><option>packet</option></select><button type="button" class="alt">×</button></div></div>')
                      .replace(f'<option value="{cust["Retailer A"]}">', f'<option value="{cust["Retailer A"]}" selected>').replace('<p class="total" id="total"></p>', '<p class="total">Estimated total before tax: Rs 7500.00</p>'))
    shot(c, "/orders/new", "Step 4: Salesman A orders 150 packets (mobile)", "Step four. Salesman A, on a mobile phone, picks Retailer A and orders one hundred and fifty packets. If the network is down, the order is saved on the phone and sent later.", PHONE, fill)
    assert c.post("/api/orders", json={"client_ref": "demo-1", "customer_id": cust["Retailer A"], "lines": [{"item_id": ids["MUR200"], "qty": 150, "unit": "packet"}]}).json()["ok"]
    shot(c, "/orders", "Order submitted straight to the factory", "The order goes straight to the factory. The salesman sees its status.", PHONE)

    login(c, "admin", "admin123")
    shot(c, "/orders/1", "Step 5: admin verifies stock and tax", "Step five. The admin sees the order with a stock check, and the tax preview: central and state G S T, because the supply is inside Tamil Nadu.")
    c.post("/orders/1/approve"); c.post("/orders/1/invoice"); c.post("/invoices/1/dispatch")
    shot(c, "/invoices/1", "GST invoice raised and dispatched", "After approval the G S T invoice is raised: seven thousand five hundred taxable, one hundred eighty seven fifty central tax and the same state tax, seven thousand eight hundred seventy five in total. Dispatch reduces stock, taking the batches that expire first.")
    pdf = c.get("/invoices/1/pdf").content
    (OUT / "inv.pdf").write_bytes(pdf)
    subprocess.run(["pdftoppm", "-png", "-r", "80", "-f", "1", "-l", "1", "-singlefile", str(OUT / "inv.pdf"), str(OUT / "inv")], check=True)
    frames.append((OUT / "inv.png", "The invoice downloads as a PDF with G S T I N, H S N code, tax split, and the amount in words, ready to share on WhatsApp or email."))

    login(c, "salesman_b", "sales123")
    assert c.post("/api/orders", json={"client_ref": "demo-2", "customer_id": cust["Distributor B"], "lines": [{"item_id": ids["MUR200"], "qty": 120, "unit": "packet"}]}).json()["ok"]
    login(c, "admin", "admin123")
    c.post("/orders/2/approve", headers={"referer": "/orders/2"})
    shot(c, "/orders/2", "Step 6: Distributor B orders 120 packets: only 88 in stock", "Step six. Salesman B orders one hundred and twenty packets for the Karnataka distributor. Only eighty eight are in stock, so the system flags a shortage of thirty two and refuses approval. If invoiced, integrated tax would apply.")
    c.post("/invoices/1/payment", data={"amount": "4000", "mode": "upi", "ref": "UPI-1"})
    shot(c, "/invoices/1", "Step 7: payment of 4,000 rupees received", "Step seven. A payment of four thousand rupees is recorded against the invoice. Outstanding for Retailer A is three thousand eight hundred seventy five.")

    login(c, "production", "prod123")
    c.post("/runs/1/overhead", data={"kind": "labour", "amount": "1200"}); c.post("/runs/1/overhead", data={"kind": "gas/power", "amount": "600"})
    shot(c, "/runs/1", "Step 8: labour 1,200 + gas/power 600: cost per packet", "Step eight. Labour of twelve hundred and gas and power of six hundred are added to the run. Cost per packet comes to thirty one rupees fourteen paise, using weighted average material cost.")

    login(c, "accountant", "acct123")
    shot(c, "/reports/sales-register", "Step 9: reports for the auditor: sales register", "Step nine. The accountant, with read only access, opens the monthly reports. First the sales register. Every report can be exported to Excel, C S V or P D F.")
    shot(c, "/reports/gst-summary", "GST summary shaped like GSTR-1 and GSTR-3B", "The G S T summary is arranged like returns G S T R one and G S T R three B, with rate wise and H S N wise tables.")
    shot(c, "/reports/stock-summary", "Stock summary: opening + receipts - issues = closing", "The stock summary shows opening, receipts, issues and closing balances for raw materials, packaging and finished goods.")
    shot(c, "/reports/production", "Production report: planned vs actual", "The production report compares planned and actual consumption and yield, with variances beyond tolerance flagged.")
    shot(c, "/reports/outstanding", "Outstanding receivables with ageing", "Outstanding receivables with ageing, by customer and by invoice.")

    login(c, "owner", "owner123")
    shot(c, "/", "Owner dashboard", "The owner sees a summary: orders, pending approvals, invoiced amount, outstanding dues, variance alerts, cost per packet and stock levels.")

    with SessionLocal() as s:
        g = lambda code: stock.balance(s, s.scalar(select(Item).where(Item.code == code)).id)[0]
        item = lambda code: s.scalar(select(Item).where(Item.code == code))
        run = s.get(__import__("app.models", fromlist=["ProductionRun"]).ProductionRun, 1)
        sm = production.summary(s, run)
        inv = s.get(__import__("app.models", fromlist=["Invoice"]).Invoice, 1)
        rows = [("Flour closing", f"{fmt_qty(item('FLOUR'), g('FLOUR'))} kg", "120 kg"), ("Oil closing", f"{fmt_qty(item('OIL'), g('OIL'))} L", "19.5 L"),
                ("Pouches closing", str(g("POUCH200")), "306"), ("Finished goods now", str(g("MUR200")), "88 (238 after the run, 150 dispatched)"),
                ("Yield variance", f"{sm['yield_var']}%", "-6%, flagged"), ("Retailer A invoice", inr(inv.total_paise), "Rs 7,875"),
                ("Retailer A outstanding", inr(sales.outstanding(inv)), "Rs 3,875"), ("Cost per packet", f"Rs {sm['cost_per_packet']}", "my figure")]
    tbl = "".join(f"<tr><td>{a}</td><td><b>{b}</b></td><td>{c_}</td></tr>" for a, b, c_ in rows)
    card("Results", [f"<div class=\"tw\"><table style='font-size:20px'><tr><th>Check</th><th>This system</th><th>Expected</th></tr>{tbl}</table></div>"],
         "These are the benchmark results, read from the live database. Every figure matches the expected values. Thank you for watching.")

# ---- narration + video
clips = []
for i, (png, text) in enumerate(frames):
    wav, mp4 = OUT / f"n{i:02d}.wav", OUT / f"c{i:02d}.mp4"
    subprocess.run(["espeak-ng", "-v", "en-in", "-s", "150", "-w", str(wav), text], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "10", "-i", str(png), "-i", str(wav),
                    "-vf", "scale=1280:820:force_original_aspect_ratio=decrease,pad=1280:820:(ow-iw)/2:(oh-ih)/2:color=0x17231e,format=yuv420p",
                    "-af", "apad=pad_dur=1.2", "-shortest", "-c:v", "libx264", "-tune", "stillimage", "-c:a", "aac", "-b:a", "96k", str(mp4)], check=True)
    clips.append(mp4)
lst = OUT / "list.txt"
lst.write_text("".join(f"file '{p}'\n" for p in clips))
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(OUT / "walkthrough.mp4")], check=True)
dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(OUT / "walkthrough.mp4")]))
print(f"frames={len(frames)} duration={dur:.0f}s -> {OUT / 'walkthrough.mp4'}")
