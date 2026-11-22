"""Accounting and government-format exports. Nothing here calls an external service."""
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime

from .config import FACTORY
from .money import paise_str

STATE_CODES = {"tamil nadu": "33", "karnataka": "29", "kerala": "32", "andhra pradesh": "37", "telangana": "36",
               "maharashtra": "27", "gujarat": "24", "delhi": "07", "puducherry": "34"}


def state_code(state: str, gstin: str | None = None) -> str:
    return (gstin or "")[:2] if gstin and gstin[:2].isdigit() else STATE_CODES.get(state.strip().lower(), "33")


# ------------------------------------------------------------------ Tally
def tally_xml(invoices, payments, company: str | None = None) -> bytes:
    """Tally Prime / ERP 9 'Import Data' envelope: one Sales voucher per invoice and one Receipt per payment.
    Ledgers named below (party names, 'Sales - GST', 'Output CGST/SGST/IGST', 'Cash', 'Bank') must exist in Tally."""
    env = ET.Element("ENVELOPE")
    head = ET.SubElement(env, "HEADER")
    ET.SubElement(head, "TALLYREQUEST").text = "Import Data"
    data = ET.SubElement(ET.SubElement(ET.SubElement(env, "BODY"), "IMPORTDATA"), "REQUESTDESC")
    ET.SubElement(data, "REPORTNAME").text = "Vouchers"
    ET.SubElement(ET.SubElement(data, "STATICVARIABLES"), "SVCURRENTCOMPANY").text = company or FACTORY["name"]
    msg = ET.SubElement(ET.SubElement(env.find("BODY/IMPORTDATA"), "REQUESTDATA"), "TALLYMESSAGE")
    msg.set("xmlns:UDF", "TallyUDF")

    def entry(v, name, amount_paise, debit, party=False):
        e = ET.SubElement(v, "ALLLEDGERENTRIES.LIST")
        ET.SubElement(e, "LEDGERNAME").text = name
        ET.SubElement(e, "ISDEEMEDPOSITIVE").text = "Yes" if debit else "No"
        if party:
            ET.SubElement(e, "ISPARTYLEDGER").text = "Yes"
        # Tally convention: debit amounts are negative, credit amounts positive
        ET.SubElement(e, "AMOUNT").text = ("-" if debit else "") + paise_str(amount_paise)

    def voucher(kind, number, d, party, narration):
        v = ET.SubElement(msg, "VOUCHER", VCHTYPE=kind, ACTION="Create", OBJVIEW="Accounting Voucher View")
        ET.SubElement(v, "DATE").text = f"{d:%Y%m%d}"
        ET.SubElement(v, "VOUCHERTYPENAME").text = kind
        ET.SubElement(v, "VOUCHERNUMBER").text = number
        ET.SubElement(v, "PARTYLEDGERNAME").text = party
        ET.SubElement(v, "NARRATION").text = narration
        return v

    for i in invoices:
        if i.status != "ISSUED":
            continue
        v = voucher("Sales", i.inv_no, i.date, i.customer.name, f"Order {i.order.order_no}; place of supply {i.place_of_supply}")
        entry(v, i.customer.name, i.total_paise, True, party=True)
        entry(v, "Sales - GST", i.taxable_paise, False)
        for label, amt in (("Output CGST", i.cgst_paise), ("Output SGST", i.sgst_paise), ("Output IGST", i.igst_paise)):
            if amt:
                entry(v, label, amt, False)
    for p in payments:
        if p.invoice.status != "ISSUED":
            continue
        v = voucher("Receipt", f"RCPT-{p.id:04d}", p.date, p.customer.name, f"Against {p.invoice.inv_no} ({p.mode} {p.ref or ''})".strip())
        entry(v, "Cash" if p.mode == "cash" else "Bank", p.amount_paise, True)
        entry(v, p.customer.name, p.amount_paise, False, party=True)
    ET.indent(env)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(env, encoding="utf-8")


# ------------------------------------------------- e-Invoice / e-Way bill
def _amt(p: int) -> float:
    return float(paise_str(p))  # JSON numbers for the schema; derived from exact paise


def mock_irn(inv) -> str:
    """Real IRNs are SHA-256 of supplier GSTIN + FY + doc type + doc no. This reproduces that recipe locally;
    it is NOT registered with the IRP, so it is not a valid IRN."""
    return hashlib.sha256(f"{FACTORY['gstin']}{inv.fy}INV{inv.inv_no}".encode()).hexdigest()


def einvoice_json(inv) -> dict:
    c = inv.customer
    items = [{"SlNo": str(n), "PrdDesc": l.description, "IsServc": "N", "HsnCd": l.hsn or "", "Qty": l.qty, "Unit": "PCS",
              "UnitPrice": _amt(l.rate_paise), "TotAmt": _amt(l.taxable_paise), "AssAmt": _amt(l.taxable_paise), "GstRt": l.gst_bp / 100,
              "IgstAmt": _amt(l.igst_paise), "CgstAmt": _amt(l.cgst_paise), "SgstAmt": _amt(l.sgst_paise),
              "TotItemVal": _amt(l.taxable_paise + l.cgst_paise + l.sgst_paise + l.igst_paise)} for n, l in enumerate(inv.lines, 1)]
    return {
        "_note": "MOCK payload in the NIC e-invoice (v1.1) shape. Not sent to any government portal; the IRN is locally computed.",
        "Version": "1.1", "Irn": mock_irn(inv),
        "TranDtls": {"TaxSch": "GST", "SupTyp": "B2B", "RegRev": "N", "IgstOnIntra": "N"},
        "DocDtls": {"Typ": "INV", "No": inv.inv_no, "Dt": f"{inv.date:%d/%m/%Y}"},
        "SellerDtls": {"Gstin": FACTORY["gstin"], "LglNm": FACTORY["name"], "Addr1": FACTORY["address"], "Loc": FACTORY["state"],
                       "Pin": 620001, "Stcd": state_code(FACTORY["state"], FACTORY["gstin"])},
        "BuyerDtls": {"Gstin": c.gstin or "URP", "LglNm": c.name, "Pos": state_code(inv.place_of_supply, c.gstin), "Addr1": c.address or c.state,
                      "Loc": c.state, "Pin": 0, "Stcd": state_code(c.state, c.gstin)},
        "ItemList": items,
        "ValDtls": {"AssVal": _amt(inv.taxable_paise), "CgstVal": _amt(inv.cgst_paise), "SgstVal": _amt(inv.sgst_paise),
                    "IgstVal": _amt(inv.igst_paise), "TotInvVal": _amt(inv.total_paise)},
        "AckNo": int(mock_irn(inv)[:12], 16) % 10**15, "AckDt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def ewaybill_json(inv, vehicle_no: str = "TN00AA0000", distance_km: int = 0) -> dict:
    c = inv.customer
    return {
        "_note": "MOCK e-way bill Part A/B payload in the NIC bulk-upload shape. Not sent to the e-way bill portal. "
                 "An e-way bill is normally required only when the consignment value exceeds Rs 50,000.",
        "supplyType": "O", "subSupplyType": 1, "docType": "INV", "docNo": inv.inv_no, "docDate": f"{inv.date:%d/%m/%Y}",
        "fromGstin": FACTORY["gstin"], "fromTrdName": FACTORY["name"], "fromAddr1": FACTORY["address"], "fromPlace": FACTORY["state"],
        "fromPincode": 620001, "fromStateCode": int(state_code(FACTORY["state"], FACTORY["gstin"])),
        "toGstin": c.gstin or "URP", "toTrdName": c.name, "toAddr1": c.address or c.state, "toPlace": c.state, "toPincode": 0,
        "toStateCode": int(state_code(c.state, c.gstin)), "transactionType": 1,
        "totalValue": _amt(inv.taxable_paise), "cgstValue": _amt(inv.cgst_paise), "sgstValue": _amt(inv.sgst_paise),
        "igstValue": _amt(inv.igst_paise), "totInvValue": _amt(inv.total_paise), "transMode": 1, "transDistance": distance_km,
        "vehicleNo": vehicle_no, "vehicleType": "R",
        "itemList": [{"productName": l.description, "hsnCode": int(l.hsn) if (l.hsn or "").isdigit() else 0, "quantity": l.qty, "qtyUnit": "PCS",
                      "taxableAmount": _amt(l.taxable_paise), "sgstRate": l.gst_bp / 200 if l.sgst_paise else 0,
                      "cgstRate": l.gst_bp / 200 if l.cgst_paise else 0, "igstRate": l.gst_bp / 100 if l.igst_paise else 0} for l in inv.lines],
    }
