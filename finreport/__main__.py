"""CLI: python -m finreport <command>."""
from __future__ import annotations

import argparse
from pathlib import Path

from . import ai_rules, anomalies, graph, networth, recurring, report
from .charts import make_charts
from .classifier import classify_all, load_rules
from .currency import fraction_digits, normalize_currency
from .models import Direction, Transaction
from .parser import load_transactions


def _load(args) -> list[Transaction]:
    override = Direction(args.direction) if getattr(args, "direction", None) else None
    txns = load_transactions(args.file, direction_override=override)
    rules = (ai_rules.effective_rules(Path(args.rules), Path(args.approved_rules))
             if args.approved_rules else load_rules(args.rules))
    classify_all(txns, rules)
    return txns


def _currencies(txns: list[Transaction]) -> list[str]:
    return sorted({normalize_currency(t.currency) for t in txns})


def _select_currency(txns: list[Transaction], code: str) -> list[Transaction]:
    try:
        code = normalize_currency(code)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    present = _currencies(txns)
    if code not in present:
        raise SystemExit(f"currency {code} not present in file; found: {', '.join(present) or 'none'}")
    return [t for t in txns if normalize_currency(t.currency) == code]


def _require_single_currency(txns: list[Transaction], code: str | None, command: str) -> tuple[list[Transaction], str]:
    """Resolve the one denomination a chart/graph may depict."""
    present = _currencies(txns)
    if code is not None:
        return _select_currency(txns, code), normalize_currency(code)
    if len(present) > 1:
        raise SystemExit(
            f"{command} cannot combine currencies {', '.join(present)}; select one with --currency")
    return txns, (present[0] if present else "CHF")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="finreport", description="Analyse BPS bank CSV exports.")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--file", required=True, help="BPS CSV export")
        sp.add_argument("--rules", default="rules.json", help="classification rules (default: rules.json)")
        sp.add_argument("--approved-rules", help="optional reviewed AI rules appended after --rules")
        sp.add_argument("--direction", choices=["in", "out"], default=None,
                        help="force direction for all rows (auto-detected otherwise)")
        sp.add_argument("--currency", default=None,
                        help="restrict to one currency (e.g. CHF); report/networth/recurring keep currencies separate when omitted")

    r = sub.add_parser("report", help="monthly & yearly income/expense report")
    common(r)
    r.add_argument("--year", type=int, default=None, help="restrict to one year")
    r.add_argument("--min-count", type=int, default=1, help="min transactions to list a counterparty")

    recurring_cmd = sub.add_parser("recurring", help="recurring-payment candidates over full file history")
    common(recurring_cmd)
    anomaly_cmd = sub.add_parser("anomalies", help="amount outliers against earlier counterparty history")
    common(anomaly_cmd)

    n = sub.add_parser("networth", help="running account balance & networth curve")
    common(n)
    n.add_argument("--config", default="config.json", help="opening balance/networth config")
    n.add_argument("--csv", default=None, help="export the series to this CSV path")
    n.add_argument("--year", type=int, default=None, help="restrict to one year")

    g = sub.add_parser("graph", help="counterparty graph (self-contained HTML)")
    common(g)
    g.add_argument("--min-count", type=int, default=1, help="min transactions for a node")
    g.add_argument("--out", default="graph.html", help="output HTML path")

    c = sub.add_parser("charts", help="PNG charts (networth, monthly, categories)")
    common(c)
    c.add_argument("--config", default="config.json", help="opening balance/networth config")
    c.add_argument("--outdir", default="output", help="output directory for PNGs")

    args = p.parse_args(argv)

    if args.command == "report":
        txns = _load(args)
        if args.currency is not None:
            txns = _select_currency(txns, args.currency)
        if args.year is not None:
            txns = [txn for txn in txns if txn.date.year == args.year]
        date_range = (f" ({min(t.date for t in txns)} .. {max(t.date for t in txns)})"
                      if txns else "")
        print(f"Loaded {len(txns)} transactions{date_range}\n")
        print("=== Monthly ===")
        print(report.monthly_report(txns))
        print()
        print("=== Yearly ===")
        print(report.yearly_report(txns))
        print()
        print(f"=== Counterparties (min count {args.min_count}) ===")
        print(report.counterparty_report(txns, min_count=args.min_count))
        print()
        print("=== Uncategorized (add rules for these) ===")
        print(report.uncategorized_report(txns))

    elif args.command == "recurring":
        txns = _load(args)
        if args.currency is not None:
            txns = _select_currency(txns, args.currency)
        print("Cadence estimates, not confirmed obligations. Full file history; at least 3 payments per group.")
        print("Trend: latest amount vs earlier median. Days past due: relative to last account/currency activity, not today.\n")
        print(recurring.recurring_report(txns))

    elif args.command == "anomalies":
        txns = _load(args)
        if args.currency is not None:
            txns = _select_currency(txns, args.currency)
        print("Review candidates, not confirmed errors. At least 3 strictly earlier payments per group.")
        print("Amount > median + max(3 × MAD, 15 native units), or > 3 × median; currencies stay separate.\n")
        print(anomalies.anomaly_report(txns))

    elif args.command == "networth":
        txns = _load(args)
        if args.currency is not None:
            txns = _select_currency(txns, args.currency)
            currency = normalize_currency(args.currency)
        else:
            currency = networth.config_currency(args.config)
        ob, onw = networth.load_config(args.config, currency)
        points = networth.compute_series(txns, ob, onw, opening_currency=currency)
        if args.year is not None:
            points = [point for point in points if point.date.year == args.year]
        if not Path(args.config).exists():
            print(f"note: {args.config} not found — using opening balance/networth = 0")
        digits = fraction_digits(currency)
        print(f"Opening {currency}: balance {ob:,.{digits}f} | networth {onw:,.{digits}f}\n")
        if any(len({txn.account for txn in txns if txn.currency == code}) > 1 for code in _currencies(txns)):
            print("Multiple accounts: account balances start at zero within each currency; source-wide openings are not allocated.\n")
        print(networth.render_table(points))
        if args.csv:
            networth.export_csv(points, args.csv)
            print(f"\nseries written to {args.csv}")

    elif args.command == "graph":
        txns = _load(args)
        txns, _cur = _require_single_currency(txns, args.currency, "graph")
        nodes = graph.build_graph(txns, min_count=args.min_count)
        Path(args.out).write_text(graph.render_html(nodes), encoding="utf-8")
        print(f"graph with {len(nodes)} counterparties written to {args.out}")

    elif args.command == "charts":
        txns = _load(args)
        txns, currency = _require_single_currency(txns, args.currency, "charts")
        if not txns and not args.currency:
            currency = networth.config_currency(args.config)
        ob, onw = networth.load_config(args.config, currency)
        written = make_charts(txns, args.outdir, ob, onw, opening_currency=currency)
        for w in written:
            print(f"wrote {w}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
