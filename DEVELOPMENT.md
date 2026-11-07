# FinancialReport — Delivery roadmap

Implemented capability details and operational boundaries live in
`DEVELOPMENT_PLAN.md`. This file tracks feature delivery; completed work stays
listed rather than disappearing from the roadmap.

## Delivery tracker

| Order | # | Feature | Status |
|---|---|---|---|
| Completed | 9 | Automatic local classification and reviewed rules | Implemented |
| Completed | 14 | Upload-first persistent imports and local model tiers | Implemented |
| Completed | 12 | Counterparty history panel | Implemented |
| Completed | 7 | Recurring payments detector | Implemented |
| Completed | 10 | Anomaly flags | Implemented |
| 1 | 8 | Period comparisons (MoM / YoY) | Planned |
| 2 | 11 | Multi-file (multi-bank) support | Planned |
| 3 | 13 | Dark mode, print/PDF and calendar view | Planned |

Remaining delivery order: **8 → 11 → 13**. A feature becomes
implemented only after its user-facing path has been exercised.
Per-category monthly budgets and a cash-flow waterfall chart remain out of scope.

## Feature sketches

### 7 — Recurring payments detector (implemented)

`finreport/recurring.py` powers `python -m finreport recurring`, `/api/recurring`
and the recurring-payment dashboard card. Groups require at least three payments
with the same raw counterparty name, account, direction and currency; shared
classification labels never merge separate counterparties. Blank names are excluded.

Cadence requires both a median gap and a strict majority of gaps inside an
inclusive day window: weekly 6–8, biweekly 12–16, monthly 26–35, yearly 350–380.
Other groups remain visible as irregular with no estimated due date. Same-day
payments are retained but cannot establish cadence. Monthly estimates preserve
month-end or the median payment day, clipped to shorter months; yearly estimates
handle leap days.

The card shows count, cadence, average amount, latest amount change against the
earlier median, last paid and estimated next due. Decimal calculations preserve
currency precision. A zero baseline has an amount change instead of a percentage;
an ambiguous latest payment date has no trend. Days past the estimate use the last
recorded activity in the same account/currency, never today's date. These are
candidates, not confirmed obligations or proof of missed payments.

Detection uses the full file history. The card respects currency, direction and
counterparty selection/search, not date/year/category filters. Clicking a name
opens its classification label's full-history panel. Clearing, failed loads and
dataset switches clear old results; late responses cannot replace a newer dataset.

Verified through focused analysis/CLI and API tests, the full regression suite,
an actual demo CLI run and desktop/mobile browser checks with isolated fixtures.
Out of scope: cash-flow forecasting, cross-bank recurrence, manual cadence editing.

### 8 — Period comparisons (MoM / YoY)

Frontend-only: "vs previous period" deltas under the Income and Expenses KPIs
(derived from the active filters), and a "compare to last year" overlay on
the monthly chart. Missing prior-period data shows as empty, never a
misleading 0%.

### 9 — Local AI → automatic and reviewed rules (implemented)

The optional review panel selects counterparties without explicit rules, requests local
llama.cpp suggestions, and lets the user edit/approve categories and labels.
Approved rules match full counterparty names, ignoring case, including previously
saved approvals. Authored substring rules keep priority.
Approved rules are private and profile-isolated, never written to the public
demo rules. Stale reviews are rejected; writes are atomic. Reports refresh after
approval. The CLI accepts reviewed rules via `--approved-rules` but does not run
inference. Existing-rule editing/deletion remains manual JSON editing.

Docker Compose provides an isolated inference worker, a localhost-facing app,
and a separate initializer for model downloads. See `README.md` for setup,
privacy boundaries, CPU/Metal tradeoffs and operations.

Imports now normalize supported statement formats and automatically categorize
unruled counterparties before opening a saved dashboard. Automatic decisions
remain provisional and separate from approved rules. `.env` selects a low,
medium or high local model. Original files, normalization evidence, rule
snapshots and active selection persist in profile-isolated workspaces; see
`DEVELOPMENT_PLAN.md` for the implemented boundaries and `README.md` for usage.

### 10 — Anomaly flags (implemented)

`finreport/anomalies.py`, the `anomalies` CLI and `/api/anomalies` flag amounts
strictly above `median + max(3 × MAD, 15 native units)` or `3 × median`.
The floor is CHF 15 for CHF, and 15 units of the native currency otherwise;
no exchange conversion is implied. At least three strictly earlier payments
to the same raw counterparty/account/direction/currency are required. Same-day
transactions cannot influence each other's baseline, and future data cannot
retroactively flag a first-time payment. Duplicate IDs remain separate occurrences.

The transactions API includes each flag's Decimal thresholds and explanation.
The dashboard has accessible reason badges and an anomalies-only filter; other
filters intersect it and reset clears it. These are review candidates, not errors.
Analysis/API regressions, the actual CLI and browser filtering/precision flows
have been exercised. Out of scope: ML models and per-user thresholds.

### 11 — Multi-file (multi-bank) support

Load more than one bank CSV side by side with a per-source filter; KPIs and
charts aggregate over the selected sources. Each transaction keeps its own
file's running balance (per-account, not merged); the networth curve uses a
single primary source. Out of scope: merged cross-bank networth,
cross-bank round-trip deduplication, per-bank opening-balance reconciliation.

### 12 — Counterparty history panel (implemented)

Frontend-only: selecting a counterparty from the table, flows chart or network
opens its case file: incoming, outgoing, net, count, first/last dates and average
transaction amount, plus monthly incoming/outgoing bars. Use the selected label
(the dashboard's existing grouping) and currency over the full dataset history,
independent of date, year, category and direction filters. Average means gross
flow divided by transaction count. Missing months show zero; currencies never
combine. Hide the panel when selection is cleared, a dataset changes or closes,
and show an explicit empty state if the selection has no activity in a currency.

Verified in the browser with the demo dataset: table/chart/network drilldown,
full-history totals under restrictive filters, clear/close/reload transitions,
currency isolation and precision, missing months, and no-activity states.

### 13 — Cosmetic & quality

Dark mode (persisted, charts re-rendered with theme-appropriate colors), a
dependency-free print / save-as-PDF path via a print stylesheet, and a
calendar month view where clicking a day filters transactions to that date.

## Ground rules for future work

- Frontend: a single-page dashboard with vanilla JS/CSS, no build step or CDN;
  charts use the locally bundled `static/plotly.min.js`.
- Backend: the CLI stays stdlib-only; new analysis goes in a
  `finreport/<module>.py` exposed through a thin FastAPI endpoint — no logic
  duplication between the CLI and the web app.
- Money: `Decimal` in Python, integer cents in the browser; never compare
  floats.
- Scope: bank transactions only. Do not infer total wealth, market gains,
  other-bank balances, or crypto.
- The app is local-only: bind to `127.0.0.1`, and keep all real data under
  `private/` (see the privacy section of `README.md`).
