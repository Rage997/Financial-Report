# FinancialReport

Turn bank statements into a dashboard for income, expenses and cash flow. Upload a file, review its categories, and explore your transactions. Your data stays on the machine running the app, with optional local AI through llama.cpp—no cloud model required.

## Get started

You'll need Docker Compose and several GB of free disk space.

```bash
[ -e .env ] || cp .env.example .env
mkdir -p private
```

Set these values in `.env`:

```dotenv
FINREPORT_PROFILE=private
FINREPORT_AI_TIER=low
```

Then start the app:

```bash
docker compose up -d --build --remove-orphans
```

Open **http://localhost:8010** and upload a statement. The first start downloads llama.cpp and the selected GGUF model, so it may take a while. To try sample data, set the profile to `demo` and choose **Try the demo dataset**.

Imports stay saved locally. Under **Local AI rules**, review and approve categories; approvals match full counterparty names, ignoring case.

## Supported files and limits

- **CSV, TSV, XLSX, XLS, JSON, OFX, QFX and QIF** transaction files.
- **TXT and PDF** statements with digital, selectable text. No scans, photos or password-protected PDFs.
- Maximum upload size: **25 MiB**. JSON tables are limited to 200 columns and one million expanded cells.
- OFX correction records and ambiguous text amounts are rejected rather than guessed. Use a finalized, structured statement export instead.
- Currencies stay separate: **no exchange-rate conversion or combined currency totals**. Missing currency declarations produce a warning and default to CHF.
- Uploaded statements show **cash flow starting at zero**, not your actual bank balance or total net worth. Multiple sources can be analysed together; networth stays tied to one primary source.

## Multiple sources

Open a dataset, then use **Analysis sources** to include other saved statements.
**Show source** filters activity; **Primary source** chooses the single source
used for the dotted networth curve. Totals and comparison charts aggregate the
shown sources in one currency, but account balances never merge across sources.
Tables and filtered CSV exports retain source/account labels.

Overlapping statements are **not deduplicated**. When a source has several
accounts in one currency, account balances start at zero: a source-wide opening
balance cannot be assigned to each account. This is not consolidated wealth.
Additional sources last for this session; opening a dataset starts a new
single-source selection. The primary keeps the existing saved-selection behavior.

## Recurring payments

The dashboard identifies candidates with at least three payments to or from the
same counterparty, keeping accounts, directions and currencies separate. It shows
cadence, average amount, latest amount change, last paid and estimated next due.
Select a name to open its classification label's history.

Detection uses each source's full history; source, currency, direction and counterparty
filters apply, but date/year/category/anomaly filters do not reduce the sample. Irregular
groups have no due-date estimate. Days past an estimate are measured against the
last recorded activity in that account/currency, **not today**. Incomplete
statements cannot prove a payment was missed; these are not confirmed obligations.
No AI is needed for this analysis.

## Anomaly review

Choose **Show anomalies only** to inspect unusually large payments. Hover or
focus an **Anomaly** badge for its explanation. Each payment is compared with at
least three strictly earlier payments to the same counterparty, account,
direction and currency. The thresholds are median + max(3 × median absolute
deviation, 15 native currency units), or 3 × median. This is a deterministic
review aid, not proof of an error or fraud; no AI or exchange conversion is used.

## Period comparisons

Income and Expenses include deltas against the previous calendar month/year
or an equally long preceding custom date range. The dates below the KPIs explain
the comparison; non-date filters apply to both periods. Missing history shows
unavailable, and zero baselines have no percentage. Choose **Compare to last year**
on the monthly chart to overlay prior-year activity without filling missing data
with invented zeros. Comparisons cover available records, not verified full statements.

## Display

Use **Dark mode / Light mode** in the header. Your explicit choice is saved in
this browser; without one, the dashboard follows the system theme. Charts update
with the page, without clearing filters or changing the transaction page.

**Print / Save PDF** opens your browser's print dialog; choose **Save as PDF**
there. Output is light, landscape A4, and stays entirely on your machine.
Charts/KPIs use all filtered transactions; the transaction table prints only its
current page. The report records that page, sources, currency and filters.
Printing does not change your theme preference or selection.

The **Calendar** lets you browse months without changing the current date range.
Select a day to set From/To to that date and clear the Year filter. Daily counts
and incoming/outgoing/net totals follow source, currency and all non-date filters;
they deliberately ignore date/year filters so the surrounding month stays visible.
On small screens, select a day for its full totals below the compact grid.
An empty day means no matching recorded activity, not proof of complete coverage.

## Local AI

Choose `FINREPORT_AI_TIER` in `.env`:

| Tier | Model | Approx. download |
|---|---|---:|
| `low` | Qwen2.5 1.5B · Q4_K_M | 1.1 GB |
| `medium` | Qwen2.5 7B · Q3_K_M | 3.8 GB |
| `high` | Qwen2.5 14B · Q3_K_L | 7.9 GB |

Start with `low` on an 8 GB Mac. Download sizes are not RAM requirements. The server keeps one model loaded with an 8,192-token context. Docker on macOS uses CPU inference; native llama.cpp can use Metal. See [`.env.example`](.env.example) for GGUF filenames, download URLs and other settings.

For custom models, change the filename and download URL together. After changing models, run `docker compose down` followed by `docker compose up -d`. Pending imports use the new model; completed imports keep their saved decisions.

If an import is slow or fails:

```bash
docker compose ps -a
docker compose logs model-init
docker compose logs llama
```

CPU inference can take several minutes.

## Run without Docker

Requires Python 3.11+ and [llama.cpp](https://github.com/ggml-org/llama.cpp#quick-start) (`brew install llama.cpp` on macOS). Start the low-tier model in another terminal:

```bash
llama-server -hf Qwen/Qwen2.5-1.5B-Instruct-GGUF:Q4_K_M \
  --alias qwen2.5-1.5b-instruct-q4_k_m.gguf --host 127.0.0.1 --port 8080 \
  --ctx-size 8192 --parallel 1 --threads 4 --threads-batch 4 \
  --no-context-shift --no-webui --cors-origins localhost
```

The alias must match the selected `FINREPORT_AI_MODEL_*` value. If your build cannot initialize Metal, add `--device none --n-gpu-layers 0 --fit off` to run on CPU.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
FINREPORT_PROFILE=private FINREPORT_AI_TIER=low \
  FINREPORT_AI_URL=http://127.0.0.1:8080 \
  python -m uvicorn app:app --host 127.0.0.1 --port 8010
```

Without `FINREPORT_AI_URL`, AI is disabled; imports that need it will fail. For CLI reports without inference:

```bash
python -m finreport report --file examples/demo-transactions.csv
python -m finreport recurring --file examples/demo-transactions.csv --currency CHF
python -m finreport anomalies --file examples/demo-transactions.csv --currency CHF
python -m finreport --help
```

## Privacy and backups

- **There is no authentication. Keep the app bound to `127.0.0.1`; don't expose it publicly.** On a remote server, use SSH port forwarding.
- Local AI may process statement text, including account details. Compose isolates inference from the internet and downloads models separately without mounting financial data. Native runs rely on your host's network controls.
- Back up **all of `private/`**. It contains statements, saved imports and reviewed rules. Never share it or your `.env` file.
- `docker compose down` preserves data and models. Adding `-v` deletes downloaded model volumes.

## Development

Run tests with `python -m unittest discover -s tests -v`. Node, when installed,
also enables the frontend calculation regressions; it is not needed to run the app.

See [implemented capabilities](DEVELOPMENT_PLAN.md) and the [roadmap](DEVELOPMENT.md) for details.

No project license has been chosen yet. Bundled Plotly.js is [MIT-licensed](static/LICENSE.plotly.txt).
