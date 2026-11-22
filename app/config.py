"""Settings. Everything overridable through environment variables."""
import os
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ROOT = BASE_DIR.parent

# SQLite by default (zero setup); docker-compose points this at PostgreSQL.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'factory.db'}")
for _old in ("postgres://", "postgresql://"):  # hosts such as Render hand out these short forms
    if DATABASE_URL.startswith(_old):
        DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len(_old):]
SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")

# Variance beyond this (percent, absolute) is flagged on production runs.
VARIANCE_TOLERANCE_PCT = Decimal(os.getenv("VARIANCE_TOLERANCE_PCT", "3"))

# The seller on every invoice. Place of supply is compared against `state`.
FACTORY = {
    "name": "Annapoorna Snacks Pvt Ltd (demo)",
    "gstin": "33AAAAA0000A1Z5",
    "state": "Tamil Nadu",
    "address": "14 Industrial Estate, Tiruchirappalli, Tamil Nadu 620001",
    "phone": "0431-0000000",
}
