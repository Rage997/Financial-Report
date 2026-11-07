# FinancialReport — Development Roadmap

This file tracks planned features and the implemented local-AI import/review workflows.
Implemented capabilities are documented in `DEVELOPMENT_PLAN.md`.

## Status

| # | Feature | Status |
|---|---|---|
| 7 | Recurring payments detector | planned |
| 8 | Period comparisons (MoM / YoY) | planned |
| 9 | Local AI → automatic and reviewed rules | implemented |
| 10 | Anomaly flags | planned |
| 11 | Multi-file (multi-bank) support | planned |
| 12 | Counterparty history panel | planned |
| 13 | Cosmetic & quality (dark mode, PDF, calendar) | planned |
| 14 | Upload-first persistent statement imports and model tiers | implemented |

Suggested order for remaining work: 12 → 7 → 10 → 8 → 11 → 13. Per-category monthly
budgets and a cash-flow waterfall chart are explicitly out of scope.

## Feature sketches

### 7 — Recurring payments detector

Detect recurring payments (rent, salary, subscriptions, …) and surface
cadence, average amount, trend, last paid, and next due — so rent increases
and missed salary months are visible. New stdlib module
`finreport/recurring.py`, a CLI subcommand, an API endpoint, and a dashboard
card. Cadence is classified from the median gap between consecutive payments
(weekly / biweekly / monthly / yearly / irregular); groups with fewer than 3
transactions are excluded. Out of scope: cash-flow forecasting, cross-bank
recurrence, manual cadence editing.

### 8 — Period comparisons (MoM / YoY)

Frontend-only: "vs previous period" deltas under the Income and Expenses KPIs
(derived from the active filters), and a "compare to last year" overlay on
the monthly chart. Missing prior-period data shows as empty, never a
misleading 0%.

### 9 — Local AI → automatic and reviewed rules (implemented)

The optional review panel selects counterparties without explicit rules, requests local
llama.cpp suggestions, and lets the user edit/approve categories and labels.
Saved patterns are fixed to full counterparty names; existing rules keep priority.
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

### 10 — Anomaly flags

Flag transactions far from their own counterparty's history (e.g. rent at 2×
typical). Requires a minimum history per group; flags when the amount exceeds
`median + max(3 × MAD, 15 CHF)` or `3 × median`. UI: a badge on flagged rows,
a "show anomalies only" toggle, and a hover tooltip with the reason. Out of
scope: ML models, flagging first-time counterparties, per-user thresholds.

### 11 — Multi-file (multi-bank) support

Load more than one bank CSV side by side with a per-source filter; KPIs and
charts aggregate over the selected sources. Each transaction keeps its own
file's running balance (per-account, not merged); the networth curve uses a
single primary source. Out of scope: merged cross-bank networth,
cross-bank round-trip deduplication, per-bank opening-balance reconciliation.

### 12 — Counterparty history panel

Frontend-only: when a counterparty is drilled into, show its case file —
totals in/out/net, count, first/last dates, average — plus a monthly
in-vs-out chart over the counterparty's full history. Hidden when no
counterparty is selected.

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
