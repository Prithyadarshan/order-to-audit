# Order-to-Audit: small food manufacturer prototype

One system from a salesman's phone order to the auditor's monthly report. Every transaction is entered once and
flows into stock, production, costing and accounts. Built for the CDI, NIT Tiruchirappalli internship task.

## Run it

```bash
# Option A: one command, PostgreSQL (needs Docker)
docker compose up --build            # http://localhost:8000

# Option B: local, SQLite, no Docker
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload        # http://localhost:8000  (seeds the Section 5 data on first start)

python -m pytest -q                  # 13 tests, including the whole Section 5 benchmark
python -m app.benchmark              # prints expected vs actual for every benchmark check (resets the local DB)
python -m app.seed --reset           # reload the seed data, e.g. before recording the demo
```

| Role | Login | Sees |
| --- | --- | --- |
| Salesman | `salesman_a` / `sales123`, `salesman_b` / `sales123` | order entry (mobile), own orders and status, own customer-wise sales |
| Factory admin | `admin` / `admin123` | orders, approval, invoices, dispatch, payments, masters, recipes |
| Production | `production` / `prod123` | purchases, production runs, stock adjustments, recipes |
| Accountant | `accountant` / `acct123` | read-only invoices, stock, all reports and exports |
| Owner | `owner` / `owner123` | dashboard, invoices, stock, reports |

## Stack and why

FastAPI + SQLAlchemy + server-rendered Jinja pages, PostgreSQL via Docker Compose (SQLite when run locally).
One Python codebase keeps the money, stock and GST rules in a single tested service layer; server-rendered pages
need no build step and work on a weak phone connection. The salesman screen is a small PWA-style page with an
offline queue. The same code runs on SQLite and PostgreSQL because it only uses SQLAlchemy.

## Features against the Section 6 tiers

**Must have:** login with five roles; product, customer, raw-material and BOM masters; mobile-friendly order entry and
admin approval; GST invoice PDF; production run that updates raw and finished stock automatically; planned vs actual
consumption and yield with variance; stock summary and ledger; sales register and stock report as CSV/Excel.

**Should have:** purchases and suppliers; payments, outstanding and ageing; cost per packet with overheads and margin;
GST summary shaped like GSTR-1 / 3B; variance alerts beyond 3%; owner dashboard.

**Stretch done:** offline order capture with automatic sync (localStorage queue, idempotent `client_ref`, service
worker for the order screen); batch number and expiry date per run (stamped on the finished-goods ledger rows,
**not** first-expiry-first-out); reorder levels with low-stock flags; production planning from open orders
("make N batches"); credit notes that return stock; audit trail report; Excel and PDF exports of every report.

## Benchmark results (`python -m app.benchmark`, all 20 checks pass)

| Check | Expected | This system |
| --- | --- | --- |
| Flour closing | 120 kg | 120 kg |
| Oil closing | 19.5 L | 19.5 L |
| Pouches closing | 306 | 306 |
| Finished goods after run / after Retailer A dispatch | 238 / 88 | 238 / 88 |
| Yield variance | 188 vs 200, -6%, flagged | -6.00%, flagged |
| Oil variance | +5%, flagged | +5.00%, flagged |
| Retailer A invoice | 7,500 + 187.50 + 187.50 = 7,875 | identical |
| Retailer A outstanding | 3,875 | 3,875 |
| Distributor B | shortage of 32, IGST if invoiced | short by 32, approval blocked, preview shows IGST 300.00 |
| Cost per packet | stated by me | **Rs 31.14** (margin Rs 18.86, 37.7%) |

**Cost per packet:** weighted-average flour is (100 x 42 + 60 x 45) / 160 = Rs 43.125/kg. Materials: flour 1,725.00 +
oil 1,470.00 + spice 240.00 + pouches 194 x 2.50 = 485.00 + cartons 9 x 15 = 135.00 = **Rs 4,055**. Overheads
1,200 + 600 = Rs 1,800. Total Rs 5,855 / 188 good packets = Rs 31.14. Damaged packets are not divided in, so their
cost lands on the packets sold. Using last purchase price (45) for flour would add Rs 75 to the run (Rs 0.40 per packet).

**Other numbers the checker may look at (not in the brief's table):** cartons closing 21 (30 - 9) is -10% against the
standard 10, so it is flagged; pouches used 194 against 200 is -3.00%, exactly on the tolerance, so it is not flagged
(flagging is strictly above 3%); spice mix closing 3.8 kg.

## Assumptions

* Opening finished goods (50 packets) are valued at an assumed Rs 30/packet; the brief gives no figure. It only
  affects stock valuation and cost of goods, not any Section 5 check.
* Murukku HSN is set to 1905 for the demo; GST is 5% as instructed. Invoice totals are not rounded off to whole rupees.
* The factory is "Annapoorna Snacks Pvt Ltd (demo)", Tamil Nadu, with a dummy GSTIN. Place of supply is the
  customer's state.
* Available stock for approval = balance minus quantities promised to other approved, undispatched orders.
* Approval is refused while stock is short (the admin can reduce to available stock, edit, or reject).
* Pouches default to good + damaged packets; other packaging defaults to the standard scaled to good output.
* Payments must be linked to an invoice and cannot exceed what is outstanding. A paid invoice cannot be cancelled until the payment is dealt with.
* Dates are "today" when a document is entered; opening balances sit on the 1st of the current month.

## Known gaps

* Not done: first-expiry-first-out dispatch, partial dispatch, returns other than full credit-note cancellation,
  Tally export, e-invoice/e-way bill, GPS visit log, Tamil UI, customer-specific prices, input GST on purchases,
  monthly shared-overhead allocation (overheads are per run), password change/user management screens.
* Security hardening is basic: signed session cookie (SameSite=Lax) but no CSRF tokens, no rate limiting.
  Set `SECRET_KEY` for any real deployment.
* The Docker Compose / PostgreSQL path is written but was not run where this was developed; the tests ran on SQLite.
* There is no hosted link and no demo video in this repository: record the video from the steps in `docs/DEMO_SCRIPT.md`.
* Reports aggregate in Python, which is fine for one factory but is not tuned for very large ledgers.

## Layout

```
app/models.py        schema            app/money.py     paise, units, GST, number-to-words
app/services/        stock, production, sales, purchasing, audit (all business rules)
app/reports.py       12 reports        app/exports.py   CSV / Excel / PDF / invoice PDF
app/routes/ + templates/ + static/     web layer         app/benchmark.py  Section 5 scenario
tests/               money, ledger, benchmark, credit note, HTTP end-to-end, role access
docs/DESIGN.md       data model, stock and BOM maths, costing method
```
