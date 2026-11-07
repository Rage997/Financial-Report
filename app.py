"""FastAPI web app for finreport.

Run:  python3 -m uvicorn app:app --host 127.0.0.1 --port 8010
"""
from __future__ import annotations

import csv
import io
import re
from contextlib import asynccontextmanager
import os
from decimal import Decimal
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from finreport import ai_rules, graph, networth, report
from finreport.imports import ACTIVE_STATES, ImportStore, MAX_UPLOAD_BYTES
from finreport.settings import ai_config, load_environment
from finreport.charts import CHART_NAMES, render_chart_png
from finreport.classifier import classify_all
from finreport.currency import fraction_digits, normalize_currency
from finreport.models import Category, Transaction
from finreport.parser import load_transactions

ROOT = Path(__file__).resolve().parent
load_environment(ROOT)
PROFILE = os.environ.get("FINREPORT_PROFILE", "demo")
if PROFILE not in {"demo", "private"}:
    raise RuntimeError("FINREPORT_PROFILE must be 'demo' or 'private'")
SETTINGS = ROOT / "private" if PROFILE == "private" else ROOT
RULES = SETTINGS / "rules.json"
CONFIG = SETTINGS / "config.json"
DATA = ROOT / "private" / "data" if PROFILE == "private" else ROOT / "examples"
UPLOADS = ROOT / "private" / "uploads" / PROFILE
STATIC = ROOT / "static"
AI_CONFIGURATION = ai_config()
AI_URL = AI_CONFIGURATION.url
AI_MODEL = AI_CONFIGURATION.model
AI_GENERATION_LOCK = Lock()
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "app", "testserver"}

_IMPORT_STORE = None
_STORE_KEY = None


def _store() -> ImportStore:
    global _IMPORT_STORE, _STORE_KEY
    key = (ROOT, PROFILE, RULES, _approved_rules_path(), AI_URL, AI_MODEL)
    if _IMPORT_STORE is None or key != _STORE_KEY:
        if _IMPORT_STORE is not None:
            _IMPORT_STORE.close()
        _IMPORT_STORE = ImportStore(ROOT, PROFILE, RULES, _approved_rules_path(),
                                    AI_URL, AI_MODEL, AI_GENERATION_LOCK)
        _STORE_KEY = key
    return _IMPORT_STORE


@asynccontextmanager
async def lifespan(application):
    store = _store()
    try:
        yield
    finally:
        store.close()


app = FastAPI(title="FinancialReport", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _host_of(host_header: str) -> str | None:
    """Parse a Host authority, including bracketed IPv6 and optional ports."""
    if not host_header or any(char.isspace() for char in host_header):
        return None
    try:
        authority = urlsplit("//" + host_header)
        port = authority.port  # Access validates numeric ports and their range.
        if (authority.path or authority.query or authority.fragment
                or authority.username is not None or authority.password is not None
                or host_header.endswith(":")):
            return None
        return authority.hostname
    except ValueError:
        return None


class LocalHostBoundary:
    """Reject requests whose ``Host`` is not a trusted local application address.

    Runs before routes, static files and docs so that no endpoint is reachable
    under a foreign host. Only the host is checked, so valid localhost/IPv4/IPv6
    origins keep working regardless of port.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        hosts = [value for key, value in scope.get("headers", ()) if key == b"host"]
        host = _host_of(hosts[0].decode("latin-1")) if len(hosts) == 1 else None
        if host not in LOCAL_HOSTS:
            response = JSONResponse(
                {"detail": "requests must use a local application address"},
                status_code=403,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


app.add_middleware(LocalHostBoundary)


def _approved_rules_path() -> Path:
    return ROOT / "private" / "ai" / PROFILE / "rules.json"



def _csv_files() -> list[Path]:
    files = [
        p for directory in (DATA, UPLOADS) for p in directory.glob("*")
        if p.suffix.lower() == ".csv" and p.is_file() and not p.is_symlink()
    ]
    return sorted(files, key=lambda p: (p.parent == UPLOADS, -p.stat().st_mtime, p.name)) + _store().ready_files()


def _load(file: str | None) -> tuple[Path, list[Transaction]]:
    files = _csv_files()
    if file:
        allowed = {str(p.relative_to(ROOT)): p for p in files}
        if file not in allowed:
            raise HTTPException(404, "dataset not found in the active profile")
        p = allowed[file]
    else:
        if not files:
            raise HTTPException(404, "no CSV files found in the active profile")
        active = _store().active_file()
        p = next((item for item in files if str(item.relative_to(ROOT)) == active), files[0])
    try:
        txns = load_transactions(p)
    except ValueError as e:
        raise HTTPException(400, str(e))
    rules = ai_rules.effective_rules(RULES, _approved_rules_path())
    classify_all(txns, rules)
    _store().classify(p, txns, rules)
    return p, txns


def _financial_config(path: Path, currency: str = "CHF") -> tuple[Decimal, Decimal]:
    # Imported statements must never inherit the synthetic demo's opening capital.
    if _store().is_managed(path):
        return Decimal("0"), Decimal("0")
    return networth.load_config(CONFIG, currency)


def _select_currency(txns: list[Transaction], currency: str | None,
                     *, require_single: bool = False) -> list[Transaction]:
    available = sorted({txn.currency for txn in txns})
    if currency is None:
        if require_single and len(available) > 1:
            raise HTTPException(400, "Select a currency: " + ", ".join(available))
        return txns
    try:
        currency = normalize_currency(currency)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if currency not in available:
        raise HTTPException(400, f"No {currency} transactions in this dataset.")
    return [txn for txn in txns if txn.currency == currency]


def _financial_series(path: Path, txns: list[Transaction]) -> tuple[
        list[networth.NetworthPoint], dict[str, dict[str, str]], dict[str, str]]:
    """Keep openings and balance semantics attached to their denomination."""
    managed = _store().is_managed(path)
    opening_currency = "CHF" if managed else networth.config_currency(CONFIG)
    ob, onw = _financial_config(path, opening_currency)
    currencies = sorted({txn.currency for txn in txns})
    opening_values = {
        code: {"balance": str(ob if code == opening_currency else Decimal("0")),
               "networth": str(onw if code == opening_currency else Decimal("0"))}
        for code in currencies
    }
    bases = {code: "configured" if not managed and code == opening_currency else "cash_flow"
             for code in currencies}
    points = networth.compute_series(txns, ob, onw, opening_currency=opening_currency)
    return points, opening_values, bases




@app.get("/", response_class=HTMLResponse)
def index():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    # An upgrade must not reuse cached scripts from the previous UI contract.
    return re.sub(
        r'(/static/([\w.-]+\.(?:css|js)))"',
        lambda match: f'{match[1]}?v={(STATIC / match[2]).stat().st_mtime_ns}"',
        html,
    )


@app.get("/api/files")
def api_files():
    files = _csv_files()
    rel = [str(p.relative_to(ROOT)) for p in files]
    active = _store().active_file()
    return {"files": rel, "default": active if active in rel else None}




@app.get("/api/data")
def api_data(file: str | None = None, year: int | None = None, currency: str | None = None):
    p, txns = _load(file)
    txns = _select_currency(txns, currency)
    if year is not None:
        txns = [txn for txn in txns if txn.date.year == year]
    totals = {}
    for txn in txns:
        bucket = totals.setdefault(txn.currency, {"in": Decimal("0"), "out": Decimal("0")})
        bucket[txn.direction.value] += txn.amount
    return {
        "file": p.name,
        "count": len(txns),
        "start": min((t.date for t in txns), default=None),
        "end": max((t.date for t in txns), default=None),
        "currencies": sorted(totals),
        "totals": {code: {direction: str(value) for direction, value in bucket.items()}
                   for code, bucket in sorted(totals.items())},
        "monthly": report.monthly_rows(txns, year),
        "yearly": report.yearly_rows(txns, year),
        "counterparties": report.counterparty_rows(txns),
        "uncategorized": report.uncategorized_rows(txns),
    }


@app.get("/api/transactions")
def api_transactions(file: str | None = None, currency: str | None = None):
    p, txns = _load(file)
    txns = _select_currency(txns, currency)
    points, opening_values, bases = _financial_series(p, txns)
    return {
        "file": str(p.relative_to(ROOT)),
        "currencies": sorted(opening_values),
        "currency_digits": {code: fraction_digits(code) for code in opening_values},
        "opening_values": opening_values,
        "balance_basis_by_currency": bases,
        "transactions": [
            {"id": t.txn_id, "date": t.date.isoformat(),
             "amount": str(t.amount), "currency": t.currency, "direction": t.direction.value,
             "category": t.category.value, "counterparty": t.counterparty,
             "label": t.label or t.counterparty, "payment_type": t.payment_type,
             "balance": str(point.balance)}
            for t, point in zip(txns, points)
        ],
    }


@app.get("/api/networth")
def api_networth(file: str | None = None, year: int | None = None, currency: str | None = None):
    p, txns = _load(file)
    txns = _select_currency(txns, currency)
    points, opening_values, bases = _financial_series(p, txns)
    if year is not None:
        points = [point for point in points if point.date.year == year]
    return {
        "file": p.name,
        "currencies": sorted(opening_values),
        "opening_values": opening_values,
        "balance_basis_by_currency": bases,
        "points": [
            {"date": pt.date.isoformat(), "event": pt.event, "currency": pt.currency,
             "balance": str(pt.balance), "networth": str(pt.networth)}
            for pt in points
        ],
    }


@app.get("/api/graph", response_class=Response)
def api_graph(file: str | None = None, min_count: int = Query(2, ge=1),
              currency: str | None = None):
    _, txns = _load(file)
    txns = _select_currency(txns, currency, require_single=True)
    nodes = graph.build_graph(txns, min_count=min_count)
    return Response(graph.render_svg(nodes), media_type="image/svg+xml")


@app.get("/api/chart", response_class=Response)
def api_chart(name: str, file: str | None = None, currency: str | None = None):
    if name not in CHART_NAMES:
        raise HTTPException(404, f"unknown chart {name!r}; expected one of {CHART_NAMES}")
    p, txns = _load(file)
    txns = _select_currency(txns, currency, require_single=True)
    selected = txns[0].currency
    ob, onw = _financial_config(p, selected)
    cash_flow_only = _store().is_managed(p) or selected != networth.config_currency(CONFIG)
    return Response(render_chart_png(name, txns, ob, onw, cash_flow_only=cash_flow_only),
                    media_type="image/png")


class SuggestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str = Field(min_length=1, max_length=1024)
    counterparties: list[str] = Field(min_length=1, max_length=ai_rules.MAX_BATCH)


class ApprovedRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    counterparty: str = Field(min_length=1)
    category: Category
    label: str = Field(min_length=1, max_length=80)


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str = Field(min_length=1, max_length=1024)
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    rules: list[ApprovedRule] = Field(min_length=1, max_length=ai_rules.MAX_BATCH)


def _local_request(request: Request) -> None:
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "cross-site AI requests are not allowed")
    if origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != (
            request.url.scheme, request.url.netloc):
        raise HTTPException(403, "cross-origin AI requests are not allowed")


def _same_origin(request: Request) -> None:
    _local_request(request)
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(415, "AI requests require application/json")


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str = Field(min_length=1, max_length=1024)


class DismissRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dismissed: bool


@app.get("/api/workspace", dependencies=[Depends(_local_request)])
def api_workspace():
    from finreport.import_formats import SUPPORTED_EXTENSIONS
    files = api_files()
    jobs = _store().list()
    return {"active_file": files["default"],
            "datasets": [job for job in jobs if job["status"] == "ready"],
            "imports": jobs, "supported_extensions": list(SUPPORTED_EXTENSIONS),
            "ai": {"tier": AI_CONFIGURATION.tier, "model": AI_MODEL}}


@app.post("/api/imports", dependencies=[Depends(_local_request)])
async def api_import(file: UploadFile = File(...)):
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        record, created = await run_in_threadpool(_store().submit, file.filename or "", content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse(record, status_code=202 if created or record["status"] in
                        ACTIVE_STATES else 200)


@app.get("/api/imports/{identifier}", dependencies=[Depends(_local_request)])
def api_import_status(identifier: str):
    try:
        return _store().get(identifier)
    except KeyError as exc:
        raise HTTPException(404, "Import not found in this profile.") from exc


@app.post("/api/imports/{identifier}/retry", dependencies=[Depends(_same_origin)])
def api_import_retry(identifier: str):
    try:
        return _store().retry(identifier)
    except KeyError as exc:
        raise HTTPException(404, "Import not found in this profile.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/imports/{identifier}/cancel", dependencies=[Depends(_same_origin)])
def api_import_cancel(identifier: str):
    try:
        return _store().cancel(identifier)
    except KeyError as exc:
        raise HTTPException(404, "Import not found in this profile.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/imports/{identifier}/dismiss", dependencies=[Depends(_same_origin)])
def api_import_dismiss(identifier: str, body: DismissRequest):
    try:
        return _store().dismiss(identifier, body.dismissed)
    except KeyError as exc:
        raise HTTPException(404, "Import not found in this profile.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.delete("/api/imports/{identifier}", dependencies=[Depends(_same_origin)])
def api_import_delete(identifier: str):
    try:
        _store().delete(identifier)
    except KeyError as exc:
        raise HTTPException(404, "Import not found in this profile.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"deleted": identifier}


@app.get("/api/imports/{identifier}/source", dependencies=[Depends(_local_request)])
def api_import_source(identifier: str):
    try:
        path, name = _store().asset(identifier, "source")
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@app.get("/api/imports/{identifier}/normalized", dependencies=[Depends(_local_request)])
def api_import_normalized(identifier: str):
    try:
        path, name = _store().asset(identifier, "normalized")
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    # Do not emit spreadsheet formulas in text fields of the generated download.
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";")
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream, delimiter=";")
        header = next(reader)
        legacy_chf = "currency" not in header
        writer.writerow(header + (["currency"] if legacy_chf else []))
        for row in reader:
            for index in (0, 4, 5, 6):
                if row[index].startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
                    row[index] = "'" + row[index]
            if legacy_chf:
                row.append("CHF")
            writer.writerow(row)
    from urllib.parse import quote
    return Response(buffer.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name)})


@app.post("/api/session", dependencies=[Depends(_same_origin)])
def api_session(body: SessionRequest):
    if body.file not in {str(path.relative_to(ROOT)) for path in _csv_files()}:
        raise HTTPException(404, "Dataset not found in this profile.")
    _store().select(body.file)
    return {"file": body.file}


@app.get("/api/ai/status")
def api_ai_status():
    return {**ai_rules.status(AI_URL, AI_MODEL), "tier": AI_CONFIGURATION.tier}


@app.get("/api/ai/candidates")
def api_ai_candidates(file: str):
    p, txns = _load(file)
    rows = ai_rules.candidates(txns, ai_rules.effective_rules(RULES, _approved_rules_path()))
    actual = {}
    for txn in txns:
        key = txn.counterparty.lower()
        category = txn.category.value
        actual[key] = category if key not in actual or actual[key] == category else "uncategorized"
    for row in rows:
        row["current_category"] = actual[row["counterparty"].lower()]
    return {"file": str(p.relative_to(ROOT)), "candidates": rows, "total": len(rows)}


@app.post("/api/ai/suggest", dependencies=[Depends(_same_origin)])
def api_ai_suggest(body: SuggestRequest):
    if not AI_GENERATION_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Local AI is busy. Wait for the current batch to finish.")
    try:
        p, txns = _load(body.file)
        approved = _approved_rules_path()
        revision = ai_rules.revision(RULES, approved, p)
        rows = ai_rules.candidates(txns, ai_rules.effective_rules(RULES, approved))
        selected = ai_rules.select_candidates(rows, body.counterparties)
        proposed = ai_rules.suggestions(AI_URL, AI_MODEL, selected)
        return {"file": body.file, "revision": revision, "model": AI_MODEL, "suggestions": proposed}
    except ai_rules.ModelTimeout as exc:
        raise HTTPException(504, str(exc)) from exc
    except ai_rules.ModelError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ai_rules.StaleRules as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        AI_GENERATION_LOCK.release()


@app.post("/api/ai/apply", dependencies=[Depends(_same_origin)])
def api_ai_apply(body: ApplyRequest):
    p, _ = _load(body.file)
    approved = _approved_rules_path()
    try:
        saved = ai_rules.apply_rules(RULES, approved, p, body.revision,
                                     [rule.model_dump(mode="json") for rule in body.rules])
    except ai_rules.StaleRules as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, "Cannot save approved rules. Check permissions on the private directory.") from exc
    return {"saved": saved, "path": str(approved.relative_to(ROOT))}
