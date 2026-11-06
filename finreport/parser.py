"""Parse BPS payment/movement CSV exports.

Handles both export flavours:
- "Lista pagamenti": outgoing only, column "Conto di addebito", positive amounts.
- "Lista movimenti": in+out, signed amounts (negative = out).
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .currency import fraction_digits, normalize_currency
from .models import Direction, Transaction

DATE_HEADERS = {"data esecuzione", "data", "execution date", "date"}
AMOUNT_HEADERS = {"totale", "importo", "amount", "valore"}
COUNTERPARTY_HEADERS = {
    "beneficiario", "destinatario", "beneficiario/intestatario",
    "beneficiary", "counterparty",
}
DEBIT_HEADERS = {"conto di addebito", "debit account"}
CREDIT_HEADERS = {"conto di accredito", "credit account"}
ID_HEADERS = {"id"}
TYPE_HEADERS = {"tipo"}
CURRENCY_HEADERS = {"currency", "valuta", "währung", "devise", "ccy", "waehrung",
                    "div.", "div", "currency code", "currencycode"}

# --- Format B: comma/quoted export (date,value_date,description,amount,currency,direction,details,booking_id)
CARD_RE = re.compile(r"(?:ACQUISTO|ACCREDITO) DEBIT CARD SVIZZERA BANCA POP\. SONDRIO \(SUISS \d{2}\.\d{2}\.\d{4} / \d{2}:\d{2}:\d{2} (.+)")
ATM_RE = re.compile(r"PRELEVAMENTO ATM SVIZZERA BANCA POP\. SONDRIO \(SUISS \d{2}\.\d{2}\.\d{4} / \d{2}:\d{2}:\d{2} (.+)")
INTEREST_RE = re.compile(r"INT\. CREDITORI")
STRUCT_RE = re.compile(r" 1/(.+?) 2/")
IBAN_RE = re.compile(r"\b(?:CH[0-9]{2}[0-9A-Z]{17}|U[0-9]{6,12})\b")
BANK_CUT_RE = re.compile(
    r"\s*(?:BANCA |POSTFINANCE|UBS |CREDIT SUISSE|SWISSQUOTE|RAIFFEISEN|ZUERCHER|JPMORGAN|SBI |CREDIT AGRICOLE|LUGANO|ZURICH|BERN)", re.I)


def extract_counterparty(desc: str) -> tuple[str, str]:
    """Return (counterparty, payment_type) from a format-B description."""
    m = CARD_RE.search(desc)
    if m:
        return m.group(1).strip(), "card"
    m = ATM_RE.search(desc)
    if m:
        return f"ATM ({m.group(1).strip()})", "atm"
    if INTEREST_RE.search(desc):
        return "Interest (INT. CREDITORI)", "interest"
    m = STRUCT_RE.search(desc)
    if m:
        return m.group(1).strip(), "transfer"
    # strip an optional memo prefix before the operation keyword
    d = re.sub(r"^(.*?)(?=ACCREDITO|ORDINE DI PAGAMENTO|PRELEVAMENTO|ACQUISTO)", "", desc)
    m = IBAN_RE.search(d)
    if m:
        rest = BANK_CUT_RE.sub("", d[m.end():], count=1).strip()
        return (rest[:80] or d[:80]), "transfer"
    return desc[:80], "other"

def _amount_and_currency(raw: str, declared: str, *, context: str) -> tuple[Decimal, str]:
    # The normalizer imports these legacy header/extractor definitions. Import
    # its strict money parser lazily so both entry points use the same rules.
    from .import_formats import _amount_currency_code, parse_source_amount

    currency = (normalize_currency(declared) if declared.strip()
                else _amount_currency_code(raw, context=context) or "CHF")
    return parse_source_amount(raw, context=context, currency=currency), currency


def _validate_normalized_amount(amount: Decimal, currency: str) -> None:
    if not amount.is_finite() or amount < 0:
        raise ValueError("Normalized amounts must be finite and nonnegative.")
    try:
        if amount.quantize(Decimal(1).scaleb(-fraction_digits(currency))) != amount:
            raise ValueError(f"Amount has fractional {currency} minor units.")
    except InvalidOperation as exc:
        raise ValueError("Normalized amount exceeds supported decimal precision.") from exc


def parse_date(raw: str) -> date:
    s = raw.strip()
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"cannot parse date {raw!r}")


def _load_format_b(path: Path, direction_override: Direction | None = None) -> list[Transaction]:
    """Comma/quoted export with explicit direction column and signed amounts."""
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    rows = [r for r in csv.DictReader(io.StringIO(raw)) if any((v or "").strip() for v in r.values())]
    if not rows:
        raise ValueError(f"{path}: no data rows")
    txns: list[Transaction] = []
    for n, r in enumerate(rows, start=2):
        desc = (r.get("description") or "").strip()
        amount, currency = _amount_and_currency(
            r.get("amount") or "", r.get("currency") or "", context=f"{path.name} row {n}")
        if direction_override is not None:
            direction = direction_override
        else:
            direction = Direction.IN if (r.get("direction") or "").strip().lower() == "in" else Direction.OUT
        counterparty, ptype = extract_counterparty(desc)
        txns.append(Transaction(
            txn_id=(r.get("booking_id") or "").strip() or str(n),
            date=parse_date(r.get("date") or r.get("value_date") or ""),
            amount=abs(amount),
            direction=direction,
            counterparty=counterparty,
            payment_type=ptype,
            currency=currency,
        ))
    txns.sort(key=lambda t: (t.date, t.txn_id))
    return txns

# Currency is appended after account; earlier text columns retain their positions.
NORMALIZED_HEADERS = ("id", "date", "amount", "direction", "counterparty", "payment_type", "account", "currency")
_LEGACY_NORMALIZED_HEADERS = NORMALIZED_HEADERS[:-1]


def write_normalized(path: Path, transactions: list[Transaction]) -> None:
    """Write lossless normalized values; classification remains separate."""
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(NORMALIZED_HEADERS)
        for txn in transactions:
            _validate_normalized_amount(txn.amount, txn.currency)
            writer.writerow((txn.txn_id, txn.date.isoformat(), str(txn.amount),
                             txn.direction.value, txn.counterparty, txn.payment_type, txn.account,
                             normalize_currency(txn.currency)))


def _load_normalized(raw: str) -> list[Transaction]:
    transactions = []
    reader = csv.DictReader(io.StringIO(raw), delimiter=";")
    has_currency = reader.fieldnames is not None and "currency" in reader.fieldnames
    for number, row in enumerate(reader, start=2):
        try:
            amount = Decimal(row["amount"])
            if not row["counterparty"].strip():
                raise ValueError("invalid counterparty")
            # Files saved before the currency column existed are CHF.
            currency = normalize_currency((row.get("currency") or "").strip()) if has_currency else "CHF"
            _validate_normalized_amount(amount, currency)
            transactions.append(Transaction(row["id"], date.fromisoformat(row["date"]), amount,
                                            Direction(row["direction"]), row["counterparty"],
                                            payment_type=row["payment_type"], account=row["account"],
                                            currency=currency))
        except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
            raise ValueError(f"Invalid normalized transaction at row {number}.") from exc
    if not transactions:
        raise ValueError("Normalized file contains no transactions.")
    return transactions



def load_transactions(path: str | Path, direction_override: Direction | None = None) -> list[Transaction]:
    path = Path(path)
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    first = raw.split("\n", 1)[0]
    if next(csv.reader([first], delimiter=";")) in (list(NORMALIZED_HEADERS), list(_LEGACY_NORMALIZED_HEADERS)):
        transactions = _load_normalized(raw)
        if direction_override is not None:
            for txn in transactions:
                txn.direction = direction_override
        return transactions
    if ',"direction",' in first or first.startswith('"date"'):
        return _load_format_b(path, direction_override)
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        rows = [r for r in csv.reader(f, delimiter=";") if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ValueError(f"{path}: no data rows")
    header = [h.strip().lower() for h in rows[0]]

    def find(names: set[str]) -> int | None:
        for i, h in enumerate(header):
            if h in names:
                return i
        return None

    i_date = find(DATE_HEADERS)
    i_amount = find(AMOUNT_HEADERS)
    i_cp = find(COUNTERPARTY_HEADERS)
    i_debit = find(DEBIT_HEADERS)
    i_credit = find(CREDIT_HEADERS)
    i_id = find(ID_HEADERS)
    from .import_formats import _currency_columns
    currency_columns = _currency_columns(header, rows[1:])
    tipo_idx = [i for i, h in enumerate(header) if h in TYPE_HEADERS]
    i_type = tipo_idx[-1] if tipo_idx else None
    i_account = i_debit if i_debit is not None else i_credit

    missing = [name for name, i in (("date", i_date), ("amount", i_amount), ("counterparty", i_cp)) if i is None]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}; header={header}")

    txns: list[Transaction] = []
    for n, row in enumerate(rows[1:], start=2):
        def cell(i: int | None) -> str:
            return row[i].strip() if i is not None and i < len(row) else ""

        declared = {normalize_currency(cell(index)) for index in currency_columns if cell(index)}
        if len(declared) > 1:
            raise ValueError(f"{path.name} row {n}: conflicting currency columns")
        amount, currency = _amount_and_currency(
            cell(i_amount), next(iter(declared), ""), context=f"{path.name} row {n}")
        if direction_override is not None:
            direction = direction_override
        elif i_debit is not None and cell(i_debit):
            direction = Direction.OUT
        elif i_credit is not None and cell(i_credit):
            direction = Direction.IN
        else:
            direction = Direction.OUT if amount < 0 else Direction.IN
        txns.append(Transaction(
            txn_id=cell(i_id) or str(n),
            date=parse_date(cell(i_date)),
            amount=abs(amount),
            direction=direction,
            counterparty=cell(i_cp),
            payment_type=cell(i_type),
            account=cell(i_account),
            currency=currency,
        ))
    txns.sort(key=lambda t: (t.date, t.txn_id))
    return txns
