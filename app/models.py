"""Data model. Money = int paise. Quantities = int base units (g / ml / pcs).

The heart of the design is StockLedger: an append-only table of signed movements.
A balance is always SUM(ledger) - it is never stored or overwritten.
"""
import datetime as dt
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

ROLES = ("salesman", "admin", "production", "accountant", "owner")
RAW, PACK, FG = "RAW", "PACK", "FG"


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    full_name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(20))
    pw_hash: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Item(Base):
    """Raw material, packaging item or finished product (kind = RAW / PACK / FG)."""
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(5))
    base_unit: Mapped[str] = mapped_column(String(5))        # g | ml | pcs  (what the ledger counts in)
    display_unit: Mapped[str] = mapped_column(String(10))    # kg | L | pcs | packet
    factor: Mapped[int] = mapped_column(Integer, default=1)  # base units per display unit
    hsn: Mapped[str | None] = mapped_column(String(10))
    gst_bp: Mapped[int] = mapped_column(Integer, default=0)  # basis points, 5% = 500
    price_paise: Mapped[int] = mapped_column(BigInteger, default=0)  # selling price per packet (FG)
    units_per_carton: Mapped[int | None] = mapped_column(Integer)
    reorder_base: Mapped[int] = mapped_column(BigInteger, default=0)
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(15), default="retailer")  # retailer | distributor
    gstin: Mapped[str | None] = mapped_column(String(15))
    state: Mapped[str] = mapped_column(String(40))
    phone: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    gstin: Mapped[str | None] = mapped_column(String(15))
    phone: Mapped[str | None] = mapped_column(String(20))


class Purchase(Base):
    __tablename__ = "purchases"
    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(20), unique=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    bill_no: Mapped[str | None] = mapped_column(String(40))
    date: Mapped[dt.date] = mapped_column(Date)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    supplier = relationship("Supplier")
    lines = relationship("PurchaseLine", back_populates="purchase", cascade="all, delete-orphan")

    @property
    def total_paise(self) -> int:
        return sum(l.amount_paise for l in self.lines)


class PurchaseLine(Base):
    __tablename__ = "purchase_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty_base: Mapped[int] = mapped_column(BigInteger)
    rate_paise: Mapped[int] = mapped_column(BigInteger)      # per display unit (e.g. per kg)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    purchase = relationship("Purchase", back_populates="lines")
    item = relationship("Item")


class Bom(Base):
    """Recipe: standard quantities per batch and the expected yield in packets."""
    __tablename__ = "boms"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("items.id"), unique=True)
    yield_packets: Mapped[int] = mapped_column(Integer)
    product = relationship("Item")
    lines = relationship("BomLine", back_populates="bom", cascade="all, delete-orphan")


class BomLine(Base):
    __tablename__ = "bom_lines"
    __table_args__ = (UniqueConstraint("bom_id", "item_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty_base: Mapped[int] = mapped_column(BigInteger)
    bom = relationship("Bom", back_populates="lines")
    item = relationship("Item")


class ProductionRun(Base):
    __tablename__ = "production_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_no: Mapped[str] = mapped_column(String(20), unique=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    date: Mapped[dt.date] = mapped_column(Date)
    batches: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(12), default="ISSUED")  # ISSUED -> COMPLETED
    expected_output: Mapped[int] = mapped_column(BigInteger)
    good_output: Mapped[int | None] = mapped_column(BigInteger)
    wastage_packets: Mapped[int] = mapped_column(BigInteger, default=0)
    batch_no: Mapped[str | None] = mapped_column(String(40))
    expiry: Mapped[dt.date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    product = relationship("Item")
    lines = relationship("RunLine", back_populates="run", cascade="all, delete-orphan", order_by="RunLine.id")
    overheads = relationship("RunOverhead", back_populates="run", cascade="all, delete-orphan")


class RunLine(Base):
    __tablename__ = "run_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("production_runs.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    phase: Mapped[str] = mapped_column(String(6))             # ISSUE (raw, at start) | PACK (at completion)
    planned_base: Mapped[int] = mapped_column(BigInteger)
    actual_base: Mapped[int] = mapped_column(BigInteger)
    value_paise: Mapped[int] = mapped_column(BigInteger)      # cost taken out of stock (weighted average)
    run = relationship("ProductionRun", back_populates="lines")
    item = relationship("Item")


class RunOverhead(Base):
    __tablename__ = "run_overheads"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("production_runs.id"))
    kind: Mapped[str] = mapped_column(String(30))             # labour | gas/power | other
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    run = relationship("ProductionRun", back_populates="overheads")


class StockLedger(Base):
    """Append-only. qty_base and value_paise are signed (+ receipt, - issue)."""
    __tablename__ = "stock_ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)
    entry_date: Mapped[dt.date] = mapped_column(Date, index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    qty_base: Mapped[int] = mapped_column(BigInteger)
    value_paise: Mapped[int] = mapped_column(BigInteger)
    doc_type: Mapped[str] = mapped_column(String(15))  # OPENING PURCHASE RUN_ISSUE RUN_PACK RUN_OUTPUT RUN_OVERHEAD DISPATCH RETURN ADJUST
    doc_id: Mapped[int | None] = mapped_column(Integer)
    doc_ref: Mapped[str | None] = mapped_column(String(40))
    batch_no: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(String(160))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    item = relationship("Item")
    user = relationship("User")


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(20), unique=True)
    client_ref: Mapped[str | None] = mapped_column(String(60), unique=True)  # idempotency key for offline sync
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    salesman_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(12), default="SUBMITTED")  # SUBMITTED APPROVED INVOICED DISPATCHED REJECTED CANCELLED
    note: Mapped[str | None] = mapped_column(String(200))
    reject_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    customer = relationship("Customer")
    salesman = relationship("User")
    lines = relationship("OrderLine", back_populates="order", cascade="all, delete-orphan", order_by="OrderLine.id")
    invoice = relationship("Invoice", back_populates="order", uselist=False)

    @property
    def total_paise(self) -> int:
        return sum(l.qty * l.price_paise for l in self.lines)


class OrderLine(Base):
    __tablename__ = "order_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[int] = mapped_column(BigInteger)              # packets
    price_paise: Mapped[int] = mapped_column(BigInteger)      # per packet, before tax
    order = relationship("Order", back_populates="lines")
    item = relationship("Item")


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    inv_no: Mapped[str] = mapped_column(String(25), unique=True)
    fy: Mapped[str] = mapped_column(String(7))
    seq: Mapped[int] = mapped_column(Integer)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    date: Mapped[dt.date] = mapped_column(Date)
    place_of_supply: Mapped[str] = mapped_column(String(40))
    intra_state: Mapped[bool] = mapped_column(Boolean)
    taxable_paise: Mapped[int] = mapped_column(BigInteger)
    cgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    sgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    igst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    total_paise: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(10), default="ISSUED")  # ISSUED | CANCELLED
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("fy", "seq"),)
    order = relationship("Order", back_populates="invoice")
    customer = relationship("Customer")
    lines = relationship("InvoiceLine", back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceLine.id")
    credit_note = relationship("CreditNote", uselist=False)
    payments = relationship("Payment", back_populates="invoice")


class InvoiceLine(Base):
    """Snapshot of what was billed; later price/HSN edits never change a posted invoice."""
    __tablename__ = "invoice_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    description: Mapped[str] = mapped_column(String(120))
    hsn: Mapped[str | None] = mapped_column(String(10))
    qty: Mapped[int] = mapped_column(BigInteger)
    rate_paise: Mapped[int] = mapped_column(BigInteger)
    taxable_paise: Mapped[int] = mapped_column(BigInteger)
    gst_bp: Mapped[int] = mapped_column(Integer)
    cgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    sgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    igst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    invoice = relationship("Invoice", back_populates="lines")
    item = relationship("Item")


class CreditNote(Base):
    __tablename__ = "credit_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    cn_no: Mapped[str] = mapped_column(String(25), unique=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), unique=True)
    date: Mapped[dt.date] = mapped_column(Date)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    invoice = relationship("Invoice", back_populates="credit_note")


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    date: Mapped[dt.date] = mapped_column(Date)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    mode: Mapped[str] = mapped_column(String(15), default="cash")
    ref: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    customer = relationship("Customer")
    invoice = relationship("Invoice", back_populates="payments")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    entity: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(30))
    detail: Mapped[str | None] = mapped_column(Text)
    user = relationship("User")


class Visit(Base):
    """Salesman check-in at a customer, with GPS position when the phone provides one."""
    __tablename__ = "visits"
    id: Mapped[int] = mapped_column(primary_key=True)
    salesman_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(String(200))
    salesman = relationship("User")
    customer = relationship("Customer")
