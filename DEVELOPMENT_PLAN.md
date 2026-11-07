# FinancialReport — Implemented capabilities and delivery record

## Goal

Analyse imported machine-readable bank statements and legacy BPS CSV exports:

1. Monitor money in/out and track **cash-flow progression**, without inventing wealth.
2. **Graph** of recurring counterparties.
3. **Monthly & yearly income vs. expenses**, where transfers to own accounts
   and investments (brokers, pillar 3) are **not** expenses.

## Status and source of truth

The inventory below preserves delivered functionality, including the llama.cpp
cutover, exact reviewed-rule matching, defensive import validation, trusted-Host
boundary, restart recovery, workspace race fixes and consistent CLI year filters.
These are existing capabilities, not outstanding roadmap tasks.

`DEVELOPMENT.md` owns the ordered feature tracker and acceptance scope.
Counterparty history (#12), recurring payments (#7), anomaly flags (#10) and
period comparisons (#8) are implemented. The next delivery is **#11: multi-file
analysis**; cosmetic/quality work remains planned.

## Implemented capabilities

- **Statement ingestion** — CSV/TSV, XLSX/XLS, JSON, OFX/QFX, QIF, TXT and
  selectable-text PDF. Deterministic readers preserve dates, amounts,
  directions, duplicates and source provenance. Local AI maps unfamiliar
  columns or extracts grounded records; invalid or incomplete results fail
  rather than creating a partial dashboard. Native currencies; no OCR.
  OFX replacement/deletion records are rejected, not counted as new movements.
  Text imports reject transactions before a table header, ambiguous amount roles,
  and balance-labelled amounts; dated transaction descriptions are not discarded
  just because they resemble headers. Sparse JSON is bounded before expansion:
  at most 200 columns and one million table cells.
- **Legacy CSV ingestion** — both BPS layouts and canonical normalized CSV;
  direction auto-detection with a CLI `--direction` override.
- **Classification** — rule-based counterparty classifier
  (`rules.json`, ordered, case-insensitive substring match, **first match
  wins**); categories `income`, `expense`, `transfer`, `investment`,
  `uncategorized`; sensible defaults for card/ATM/interest rows.
- **Reporting** — CLI `python -m finreport report`: monthly and yearly
  income / expenses / transfers / investments / uncategorized / net,
  counterparty frequency & totals, and the uncategorized list (to feed new
  rules). `report --year` scopes every section, including counterparties and
  uncategorized transactions; an empty year produces an empty report.
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
  A global trusted-Host boundary covers all HTTP routes, including financial reads,
  static assets and docs. Browser mutations retain origin/fetch-site checks;
  JSON actions also require the JSON content type. This is not authentication.
- **Saved workspaces** — profile-isolated SQLite state and source/canonical/
  provenance/rule files under `private/workspaces/`; byte-identical upload reuse,
  checkpointed retries, interrupted-job recovery and persisted dataset selection.
  Reopening a ready dashboard does not require inference. Compact import history
  supports persistent dismissal/restore and explicit deletion of owned files.
  On restart, queued, interrupted and waiting-AI jobs adopt the configured model
  under the worker lease. Ready datasets and existing normalization evidence keep
  their recorded models; failed/cancelled jobs still require an explicit retry.
  Completed mutations invalidate older workspace snapshots and job polls, so late
  responses cannot undo uploads, retries or cancellation. Deletion checks the
  current selection at completion, preserving newer selections and clearing a
  dataset selected while its deletion was pending.
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
  remaining counterparties each get an isolated llama.cpp request. Decisions are cached
  by exact name, direction/payment-type context and model, then snapshotted per
  dataset. Automatic transfer/investment proposals require ownership confirmation
  and otherwise remain uncategorized. Categories remain provisional.
  Deterministic imports and AI jobs use separate queues; only model requests are
  serialized. Streaming progress reports elapsed time and generated characters,
  with cancellation that interrupts inference and prevents late result publication.
- **Local AI rule assistant** — llama.cpp suggests classifications for unruled
  counterparties in batches of up to 12. Review/edit categories and labels,
  explicitly approve, then persist profile-isolated rules under `private/ai/`.
  Approved rules match full names case-insensitively, including older saved rules.
  Authored substring rules keep priority; suggestions alone never change reports.
  Structured-output validation, stale-review rejection and atomic writes protect
  the rule boundary. CLI commands can include reviewed rules via `--approved-rules`.
- **Docker Compose** — non-root app, resident llama.cpp worker and one-off GGUF
  downloader. The downloader has no financial-data mount; the inference worker
  uses an internal-only network and a read-only model volume. The app additionally
  joins a web bridge to publish its localhost port. Public source allowlists
  exclude private data from images and release ZIPs.
  `.env` selects low/medium/high models (approximately 1.1/3.8/7.9 GB of weights,
  not memory caps), identically for native and Compose runs. Only the selected
  model is downloaded and loaded. The worker uses one slot, an 8,192-token context
  without context shifting, and four threads for CPU generation and prompt processing.
  The client uses schema-constrained `/v1/chat/completions` with SSE streaming;
  health and model-alias checks prevent requests to an unready or mismatched worker.
  Truncated output is rejected; generation deadlines remain distinct from connection
  failures. Saved imports and reviewed rules survive backend changes.
- **Counterparty history (#12)** — selecting a label in the table, flows chart
  or network opens incoming/outgoing/net totals, count, first/last dates and
  average gross transaction amount. Monthly bars cover the full dataset history,
  filling missing months with zero. The active currency is respected independently
  of date/year/category/direction filters; currencies never combine. Clearing,
  closing or reloading a dataset clears the panel. Empty currency selections have
  an explicit no-activity state. Browser-verified with demo and edge-case data.
- **Recurring payments (#7)** — deterministic stdlib analysis, the `recurring`
  CLI command, `/api/recurring?file=…&currency=…` and a dashboard card. At least
  three payments per raw counterparty/account/direction/currency are required.
  Cadence, currency-rounded average, latest-vs-prior-median trend, last paid and
  calendar-aware next-due estimates use full file history. Irregular groups have
  no due date; same-day ambiguities and zero baselines are explicit. Decimal API
  fields serialize as strings. Days past due are relative to observed account
  activity, not today; no confirmed-obligation or missing-payment claim is made.
  Currency/direction/counterparty filters apply to the card independently of
  date/year/category filters. Names link to label history; empty, failed,
  cleared and superseded dataset loads cannot retain stale recurrence results.
  Tested through CLI/API and real desktop/mobile browser flows. Docker and
  public-export allowlists include the module.
- **Anomaly flags (#10)** — stdlib `anomalies` CLI, `/api/anomalies`, transaction
  reason badges and an anomalies-only filter. Median/MAD and ratio thresholds
  use at least three strictly earlier payments, keeping exact counterparties,
  accounts, directions and currencies separate. Same-day ordering and duplicate
  IDs cannot manufacture a baseline. Thresholds use Decimal; the fixed floor
  is 15 native units, not converted CHF. Candidates are explanatory, not a fraud
  or error verdict. Unit/API and live CLI/browser flows verified.
- **Period comparisons (#8)** — frontend-only Income/Expenses KPI deltas with
  explicit comparison dates and a prior-year monthly-chart overlay. Full months
  and years use calendar periods; custom ranges use an equal preceding day count.
  Non-date filters and refund accounting apply equally to both periods. Missing
  history stays unavailable; zero baselines have no percentage. Integer-minor-unit
  calculations have browser coverage and optional Node-backed regression tests.

The networth curve is a flow-based estimate from the selected bank export. It
does **not** include market gains/losses on investments, other banks, or
other assets. Currencies are never converted or summed together; one dataset
at a time (no multi-bank consolidation).

## Remaining delivery

| Order | Feature | Status |
|---|---|---|
| 1 | #11 Multi-file / multi-bank analysis | Planned |
| 2 | #13 Dark mode, print/PDF and calendar | Planned |

See `DEVELOPMENT.md` for acceptance criteria and exclusions. Automatic
import/categorization (#14) and optional reviewed rule creation (#9) are already
implemented. Existing reviewed-rule editing/deletion remains manual JSON editing.

## Usage

```bash
# web app (demo by default; FINREPORT_PROFILE=private for your own data)
python -m uvicorn app:app --host 127.0.0.1 --port 8010   # → http://localhost:8010

# CLI
mkdir -p output
python -m finreport report   --file examples/demo-transactions.csv
python -m finreport recurring --file examples/demo-transactions.csv --currency CHF
python -m finreport anomalies --file examples/demo-transactions.csv --currency CHF
python -m finreport networth --file examples/demo-transactions.csv --csv output/networth.csv
python -m finreport graph    --file examples/demo-transactions.csv --min-count 2 --out graph.html
python -m finreport charts   --file examples/demo-transactions.csv --outdir output
```

Restart the server after backend changes — API routes and the `/static`
mount load at process startup.

## Dependencies

The report, recurring, anomalies, networth, and graph commands use the Python standard library.
The web app adds `fastapi`, `uvicorn`, and `python-multipart`; charts add
`matplotlib`. Shared local configuration uses `python-dotenv`. Import readers
use `openpyxl`, `xlrd`, `pypdf`, and `defusedxml`; no external OCR service is used.
