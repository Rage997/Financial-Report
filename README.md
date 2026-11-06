# FinancialReport

Turn bank statements into a dashboard for income, expenses and cash flow. Upload a file, review its categories, and explore your transactions. Your data stays on the machine running the app, with optional local AI through Ollama—no cloud model required.

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
docker compose up -d --build
```

Open **http://localhost:8010** and upload a statement. The first start downloads Ollama and the selected model, so it may take a while. To try sample data, set the profile to `demo` and choose **Try the demo dataset**.

Imports are saved locally, so you can reopen them without uploading again. Review AI suggestions under **Local AI rules** before approving them.

## Supported files and limits

- **CSV, TSV, XLSX, XLS, JSON, OFX, QFX and QIF** transaction files.
- **TXT and PDF** statements with digital, selectable text. No scans, photos or password-protected PDFs.
- Maximum upload size: **25 MiB**.
- Currencies stay separate: **no exchange-rate conversion or combined currency totals**. Missing currency declarations produce a warning and default to CHF.
- Uploaded statements show **cash flow starting at zero**, not your actual bank balance or total net worth. One dataset is analysed at a time.

## Local AI

Choose `FINREPORT_AI_TIER` in `.env`:

| Tier | Model | Approx. download |
|---|---|---:|
| `low` | `qwen2.5:1.5b` | 1 GB |
| `medium` | `qwen2.5:7b-instruct-q3_K_M` | 4 GB |
| `high` | `qwen2.5:14b-instruct-q3_K_L` | 8 GB |

Start with `low` on an 8 GB Mac. Download sizes are not RAM requirements. Docker on macOS uses CPU inference; native Ollama can use Metal. See [`.env.example`](.env.example) for model overrides and other settings.

After changing models, run `docker compose down` followed by `docker compose up -d`. Existing imports keep their saved decisions.

If an import is slow or fails:

```bash
docker compose ps -a
docker compose logs model-init
docker compose logs ollama
```

CPU inference can take several minutes.

## Run without Docker

Requires Python 3.11+. For local AI, install [Ollama](https://ollama.com/download), keep its cloud features disabled, and start it with `OLLAMA_NO_CLOUD=1 ollama serve` in another terminal.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
ollama pull qwen2.5:1.5b
FINREPORT_PROFILE=private FINREPORT_AI_TIER=low \
  FINREPORT_AI_URL=http://127.0.0.1:11434 \
  python -m uvicorn app:app --host 127.0.0.1 --port 8010
```

Without `FINREPORT_AI_URL`, AI is disabled; imports that need it will fail. For CLI reports without inference:

```bash
python -m finreport report --file examples/demo-transactions.csv
python -m finreport --help
```

## Privacy and backups

- **There is no authentication. Keep the app bound to `127.0.0.1`; don't expose it publicly.** On a remote server, use SSH port forwarding.
- Local AI may process statement text, including account details. “Local” means the machine running the app, not necessarily your browser's machine.
- Back up **all of `private/`**. It contains statements, saved imports and reviewed rules. Never share it or your `.env` file.
- `docker compose down` preserves data and models. Adding `-v` deletes downloaded model volumes.

## Development

Run tests with `python -m unittest discover -s tests -v`.

See [implemented capabilities](DEVELOPMENT_PLAN.md) and the [roadmap](DEVELOPMENT.md) for details.

No project license has been chosen yet. Bundled Plotly.js is [MIT-licensed](static/LICENSE.plotly.txt).
