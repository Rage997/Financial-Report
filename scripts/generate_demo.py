#!/usr/bin/env python3
"""Generate examples/demo-transactions.csv: fully synthetic, deterministic demo data.

Counterparties, addresses, amounts and dates are invented. The fixed bank-format
wording is retained only to exercise the parser's card/ATM patterns. No real file
is read; formulas over the month index make regeneration byte-for-byte reproducible.

Coverage (24 months, 2024-01 through 2025-12, opening balance 10000 CHF as of
2024-01-01):
  - income:        monthly salary, quarterly interest, broker dividends, and
                   irregular freelance payments (lumpy, not every month)
  - expense refunds: merchant refunds, classified as expense via
                   dedicated refund rules (distinct labels)
  - transfers:     own savings account deposits/returns and ATM withdrawals
  - investments:   monthly broker purchases and pension contributions (out),
                   two one-off broker withdrawals (in)
  - recurring:     rent, internet, phone, gym, insurance
  - one-off:       groceries (3x/month), fuel, books, electronics
  - uncategorized: a few plain memo rows that match no parser pattern and no
                   rule, exercising the uncategorized fallback

Format: parser format B (comma, all columns quoted):
  date,value_date,description,amount,currency,direction,details,booking_id
Amounts are signed (in positive, out negative); the parser takes abs() and the
direction column. booking_id prefix is DEMO.

Regenerate with:
  python3 scripts/generate_demo.py
"""
from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "examples" / "demo-transactions.csv"

START_YEAR = 2024
MONTHS = 24  # 2024-01 .. 2025-12

ADDR_HOME = "Via del Parco 12, 6900 Lugano"
ADDR_CITY = "Via della Fonte 42, 6900 Lugano"

BANK = "SVIZZERA BANCA POP. SONDRIO"


def money(value: float) -> Decimal:
    return Decimal(str(round(value, 2)))


class RowBuilder:
    def __init__(self) -> None:
        self.rows: list[tuple[date, str, Decimal, str]] = []

    def add(self, dt: date, desc: str, amount: float, direction: str) -> None:
        assert amount > 0, f"non-positive amount {amount} for {desc!r}"
        assert direction in ("in", "out")
        self.rows.append((dt, desc, money(amount), direction))

    def card(self, dt: date, merchant: str, amount: float, m: int, k: int) -> None:
        hh = 8 + (m * 3 + k) % 12
        mm = (m * 7 + k * 13) % 60
        ss = (m * 11 + k * 17) % 60
        desc = (f"ACQUISTO DEBIT CARD {BANK} "
                f"(SUISS {dt.strftime('%d.%m.%Y')} / {hh:02d}:{mm:02d}:{ss:02d} {merchant})")
        self.add(dt, desc, amount, "out")


def build() -> list[tuple[date, str, Decimal, str]]:
    b = RowBuilder()
    for m in range(MONTHS):
        year = START_YEAR + m // 12
        month = m % 12 + 1
        D = lambda day: date(year, month, day)  # noqa: E731

        # --- income ---
        b.add(D(1), f"ACCREDITO 1/Fictional Employer SA 2/{ADDR_HOME}",
              5800 + (m * 13) % 240, "in")
        if m % 3 == 0:  # quarterly interest
            b.add(D(28), "INT. CREDITORI INTERESSI TRIMESTRALI",
                  1.50 + m * 0.05, "in")
        # --- inconsistent freelance income (irregular, lumpy client payments) ---
        if (m * 13 + 5) % 7 < 4:
            if m % 2 == 0:
                client = "SynthiClient GmbH"
                amount = 450 + ((m * 137) % 2200) + ((m * 13) % 100) / 100
            else:
                client = "SynthiStudio Ltd"
                amount = 300 + ((m * 89) % 1800) + ((m * 29) % 100) / 100
            b.add(D(3 + (m * 7) % 25), f"ACCREDITO 1/{client} 2/{ADDR_CITY}",
                  amount, "in")

        # --- transfers (own accounts) ---
        b.add(D(3), f"ORDINE DI PAGAMENTO 1/SynthiBank Savings Account 2/{ADDR_HOME}",
              500, "out")
        if m % 4 == 2:
            b.add(D(19), f"ACCREDITO 1/SynthiBank Savings Account 2/{ADDR_HOME}",
                  350 + m * 5, "in")
        loc = "Lugano Centro" if m % 2 == 0 else "Lugano Stazione"
        b.add(D(15), f"PRELEVAMENTO ATM {BANK} "
                     f"(SUISS {D(15).strftime('%d.%m.%Y')} / 10:15:00 {loc})",
              200 + (m * 37) % 201, "out")

        # --- investments (fictional broker, both directions) ---
        b.add(D(5), f"ORDINE DI PAGAMENTO 1/SynthiBroker AG 2/{ADDR_CITY}",
              800 + (m * 29) % 161, "out")
        if m % 3 == 0:  # quarterly dividend
            b.add(D(29), f"ACCREDITO 1/SynthiBroker Dividend 2/{ADDR_CITY}",
                  45 + m * 0.25, "in")
        b.add(D(7), f"ORDINE DI PAGAMENTO 1/SynthiPension Foundation 2/{ADDR_CITY}",
              250, "out")

        # --- recurring expenses ---
        rent = 1850 if year == 2024 else 1865
        b.add(D(1), f"ORDINE DI PAGAMENTO 1/SynthiHomes AG 2/{ADDR_HOME}", rent, "out")
        b.add(D(6), f"ORDINE DI PAGAMENTO 1/SynthiNet AG 2/{ADDR_CITY}", 59.90, "out")
        b.add(D(8), f"ORDINE DI PAGAMENTO 1/SynthiPhone SA 2/{ADDR_CITY}", 32.50, "out")
        b.add(D(10), f"ORDINE DI PAGAMENTO 1/SynthiGym Club 2/{ADDR_HOME}", 65.00, "out")
        b.add(D(12), f"ORDINE DI PAGAMENTO 1/SynthiAssure SA 2/{ADDR_CITY}", 120.00, "out")

        # --- one-off card purchases ---
        for k in range(3):  # groceries, 3x per month
            b.card(D(4 + k * 10), f"SynthiMart Store {k + 1}",
                   80 + (m * 7 + k * 29) % 81 + ((m * 3 + k) % 100) / 100, m, k)
        b.card(D(18), "SynthiFuel Station 7", 60 + (m * 17) % 31 + 0.43, m, 4)
        b.card(D(11), "Vendomatic 7", 3 + (m * 5) % 6, m, 5)  # no rule: default expense
        if m % 6 == 2:  # books, every 6th month
            b.card(D(20), "SynthiBooks Shop", 25 + (m * 11) % 36, m, 6)

    # --- one-off events on fixed dates ---
    b.card(date(2024, 3, 21), "SynthiElectro Store", 449.90, 2, 7)
    b.card(date(2025, 11, 22), "SynthiElectro Store", 899.00, 22, 7)

    # --- expense refunds (in direction, classified as expense by refund rules) ---
    for dt, amount in ((date(2024, 4, 18), 24.90), (date(2024, 9, 6), 45.00),
                       (date(2025, 2, 21), 32.50), (date(2025, 8, 13), 61.20)):
        b.add(dt, f"ACCREDITO 1/SynthiMart Refund 2/{ADDR_HOME}", amount, "in")
    b.add(date(2025, 5, 30), f"ACCREDITO 1/SynthiElectro Refund 2/{ADDR_CITY}",
          89.90, "in")

    # --- one-off broker withdrawals (investment, in direction) ---
    b.add(date(2024, 7, 10), f"ACCREDITO 1/SynthiBroker AG 2/{ADDR_CITY}", 1200, "in")
    b.add(date(2025, 9, 10), f"ACCREDITO 1/SynthiBroker AG 2/{ADDR_CITY}", 1500, "in")

    # --- uncategorized fallback: plain memos, no parser pattern, no rule ---
    b.add(date(2024, 2, 17), "DIVERSO MEMO 101", 35.00, "out")
    b.add(date(2024, 8, 9), "MEMO CREDITO 202", 20.00, "in")
    b.add(date(2025, 3, 14), "DIVERSO MEMO 303", 55.00, "out")
    b.add(date(2025, 10, 5), "MEMO 404", 18.50, "out")

    return b.rows


def main() -> None:
    rows = sorted(build(), key=lambda r: (r[0], r[3], r[1]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(["date", "value_date", "description", "amount",
                    "currency", "direction", "details", "booking_id"])
        for i, (dt, desc, amount, direction) in enumerate(rows, start=1):
            signed = amount if direction == "in" else -amount
            w.writerow([dt.isoformat(), dt.isoformat(), desc,
                        f"{signed:.2f}", "CHF", direction, "",
                        f"DEMO{i:06d}"])

    total_in = sum(r[2] for r in rows if r[3] == "in")
    total_out = sum(r[2] for r in rows if r[3] == "out")
    print(f"wrote {OUT} ({len(rows)} rows)")
    print(f"range: {rows[0][0].isoformat()} .. {rows[-1][0].isoformat()}")
    print(f"total in:  {total_in:.2f} CHF")
    print(f"total out: {total_out:.2f} CHF")
    print(f"balance from 10000.00 opening: {Decimal('10000.00') + total_in - total_out:.2f} CHF")


if __name__ == "__main__":
    main()
