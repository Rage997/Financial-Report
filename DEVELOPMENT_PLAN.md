# FinancialReport — Capabilities & Roadmap

## Goal

Analyse imported machine-readable bank statements and legacy BPS CSV exports:

1. Monitor money in/out and track **cash-flow progression**, without inventing wealth.
2. **Graph** of recurring counterparties.
3. **Monthly & yearly income vs. expenses**, where transfers to own accounts
   and investments (brokers, pillar 3) are **not** expenses.

## Implemented capabilities

- **Statement ingestion** — CSV/TSV, XLSX/XLS, JSON, OFX/QFX, QIF, TXT and
  selectable-text PDF. Deterministic readers preserve dates, amounts,
  directions, duplicates and source provenance. Local AI maps unfamiliar
  columns or extracts grounded records; invalid or incomplete results fail
  rather than creating a partial dashboard. Native currencies; no OCR.
- **Legacy CSV ingestion** — both BPS layouts and canonical normalized CSV;
  direction auto-detection with a CLI `--direction` override.
- **Classification** — rule-based counterparty classifier
  (`rules.json`, ordered, case-insensitive substring match, **first match
  wins**); categories `income`, `expense`, `transfer`, `investment`,
  `uncategorized`; sensible defaults for card/ATM/interest rows.
- **Reporting** — CLI `python -m finreport report`: monthly and yearly
  income / expenses / transfers / investments / uncategorized / net,
  counterparty frequency & totals, and the uncategorized list (to feed new
  rules).
- **Native-currency accounting** — per-transaction currencies and currency-specific
  decimal precision; separate monthly/yearly/counterparty totals and running
  balances, with no implicit FX conversion or cross-currency sum. CLI `--currency`
  filters all commands; graphs/charts require a selection for mixed files.
  Legacy normalized files without a currency remain CHF-compatible.
- **Networth tracking** — opening balance / opening networth from
  `config.json`, assigned to its `currency` (legacy default CHF); running
  account balance per transaction and currency; networth curve (income −
  expenses only; transfers and investments neutral); CLI `networth` with
  currency-labelled CSV export. Year filters preserve the preceding balance.
- **Counterparty graph** — account hub → counterparty nodes, normalized by
  rule label; node size = frequency, color = category, edge width = total
  flow; self-contained offline HTML (inline SVG).
- **Charts** — networth + account balance line chart, monthly income vs.
  expenses bars, outflow category donut (matplotlib, `--outdir`).
- **Web app (FastAPI)** — single-page UI (`static/index.html`, vanilla JS,
  no build step); an **Open a file to start** welcome screen on every visit,
  one-click upload, explicit saved-dataset continuation and non-destructive
  **Close file**; durable import jobs and explicit demo opt-in;
  APIs for file selection, import status/retry/cancel, source/normalized
  downloads, KPI data, transaction series, counterparty graphs and PNG charts.
- **Saved workspaces** — profile-isolated SQLite state and source/canonical/
  provenance/rule files under `private/workspaces/`; byte-identical upload reuse,
  checkpointed retries, interrupted-job recovery and persisted dataset selection.
  Reopening a ready dashboard does not require inference. Compact import history
  supports persistent dismissal/restore and explicit deletion of owned files.
- **Imported cash flow** — uploaded datasets start at zero per currency and are
  labelled as cumulative cash flow, never given the demo's opening capital or
  represented as actual bank balances/net worth.
- **Interactive dashboard** — locally bundled Plotly.js 2.35.2 (MIT header
  retained, no CDN); a currency selector scopes all figures and exports;
  linked date, year, category, direction, and counterparty-search filters;
  period drill-down on monthly/yearly bars;
  click-to-filter on counterparty and category charts; a directed
  hub-and-spoke **counterparty network** with ranking, top-N selection, and
  click-to-drill; scrollable tables with sticky headers, sorting, pagination,
  and filtered CSV export.
- **Automatic local classification** — authored/reviewed/type rules run first;
  remaining counterparties each get an isolated Ollama request. Decisions are cached
  by exact name, direction/payment-type context and model, then snapshotted per
  dataset. Automatic transfer/investment proposals require ownership confirmation
  and otherwise remain uncategorized. Categories remain provisional.
  Deterministic imports and AI jobs use separate queues; only model requests are
  serialized. Streaming progress reports elapsed time and generated characters,
  with cancellation that interrupts inference and prevents late result publication.
- **Local AI rule assistant** — Ollama suggests classifications for unruled
  counterparties in batches of up to 12. Review/edit categories and labels,
  explicitly approve, then persist profile-isolated rules under `private/ai/`.
  Existing rules keep priority; suggestions alone never change reports.
  Structured-output validation, stale-review rejection and atomic writes protect
  the rule boundary. CLI commands can include reviewed rules via `--approved-rules`.
- **Docker Compose** — non-root app, persistent Ollama worker and one-off model
  initializer. The initializer downloads weights without mounting financial
  data; the inference worker is on an internal-only network. The app additionally
  joins a web bridge to publish its localhost port. Public source allowlists
  exclude private data from images and release ZIPs.
  `.env` selects low/medium/high models (approximately 1/4/8 GB of weights,
  not memory caps), identically for native and Compose runs. Only the selected
  model is initialized. Consecutive requests reuse weights for five minutes idle
  before unloading; prompt-sized contexts and output budgets avoid excess work.
  Generation timeouts remain distinct from connection failures.
  CPU inference uses at most four threads rather than every virtual CPU.

The networth curve is a flow-based estimate from the selected bank export. It
does **not** include market gains/losses on investments, other banks, or
other assets. Currencies are never converted or summed together; one dataset
at a time (no multi-bank consolidation).

## Roadmap (planned, not yet implemented)

See `DEVELOPMENT.md` for recurring payments, period comparisons, anomaly flags,
multi-file support, counterparty history, and cosmetic/quality improvements.
Automatic import/categorization and optional reviewed rule creation are implemented;
existing reviewed-rule editing/deletion remains manual JSON editing.

## Usage

```bash
# web app (demo by default; FINREPORT_PROFILE=private for your own data)
python -m uvicorn app:app --host 127.0.0.1 --port 8010   # → http://localhost:8010

# CLI
mkdir -p output
python -m finreport report   --file examples/demo-transactions.csv
python -m finreport networth --file examples/demo-transactions.csv --csv output/networth.csv
python -m finreport graph    --file examples/demo-transactions.csv --min-count 2 --out graph.html
python -m finreport charts   --file examples/demo-transactions.csv --outdir output
```

Restart the server after backend changes — API routes and the `/static`
mount load at process startup.

## Dependencies

The report, networth, and graph commands use the Python standard library.
The web app adds `fastapi`, `uvicorn`, and `python-multipart`; charts add
`matplotlib`. Shared local configuration uses `python-dotenv`. Import readers
use `openpyxl`, `xlrd`, `pypdf`, and `defusedxml`; no external OCR service is used.
