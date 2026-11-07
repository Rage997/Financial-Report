"""Private, resumable import jobs and exact-name automatic classification caches."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import ai_rules
from .classifier import classify_all
from .models import Category
from .parser import load_transactions, write_normalized

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ACTIVE_STATES = ("queued", "normalizing", "categorizing", "waiting_ai", "cancelling")
TERMINAL_STATES = ("ready", "failed", "cancelled")
AUTO_BATCH_SIZE = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".pending-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

def _rule_key(row: dict) -> str:
    return json.dumps([row["counterparty"].lower(), sorted(row["directions"]),
                       sorted(row["payment_types"])], ensure_ascii=False, separators=(",", ":"))


def _automatic_candidates(transactions, rules) -> list[dict]:
    return ai_rules.candidates([txn for txn in transactions if txn.category == Category.UNCATEGORIZED], rules)


class ImportStore:
    def __init__(self, root: Path, profile: str, base_rules: Path, approved_rules: Path,
                 ai_url: str, ai_model: str, generation_lock: threading.Lock,
                 *, autostart: bool = True):
        if profile not in {"demo", "private"}:
            raise ValueError("Invalid workspace profile.")
        self.root = root.resolve()
        self.directory = self.root / "private" / "workspaces" / profile
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = self.directory / "workspace.sqlite"
        self.base_rules = base_rules
        self.approved_rules = approved_rules
        # A fresh private workspace needs no hand-written classification file.
        if profile == "private" and not base_rules.exists():
            base_rules.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                with base_rules.open("x", encoding="utf-8") as stream:
                    json.dump({"rules": []}, stream)
            except FileExistsError:
                pass
        self.ai_url = ai_url
        self.ai_model = ai_model
        self.generation_lock = generation_lock
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads = []
        self._controls = {}
        self._state_lock = threading.RLock()
        self._lease = None
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS imports (
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL, extension TEXT NOT NULL,
                    status TEXT NOT NULL, stage TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
                    count INTEGER NOT NULL DEFAULT 0, error TEXT,
                    warnings TEXT NOT NULL DEFAULT '[]', model TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS automatic_rules (
                    signature TEXT NOT NULL, model TEXT NOT NULL, decision TEXT NOT NULL,
                    PRIMARY KEY (signature, model)
                );
                CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(imports)")}
            for name, definition in {
                "dismissed": "INTEGER NOT NULL DEFAULT 0", "started_at": "TEXT",
                "finished_at": "TEXT", "stage_started_at": "TEXT",
                "ai_phase": "TEXT", "ai_chars": "INTEGER NOT NULL DEFAULT 0",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE imports ADD COLUMN {name} {definition}")
            db.execute("UPDATE imports SET started_at=created_at, stage_started_at=updated_at,"
                       "finished_at=CASE WHEN status IN ('ready','failed','cancelled') THEN updated_at END "
                       "WHERE started_at IS NULL")
        os.chmod(self.database, 0o600)
        if autostart:
            self.start()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self) -> None:
        with self._state_lock:
            if self._threads:
                return
            lease = (self.directory / "worker.lock").open("a")
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lease.close()
                return  # Another app process owns this workspace's durable queue.
            self._lease = lease
            self._stop.clear()
            with self._connect() as db:
                db.execute("UPDATE imports SET status='queued', stage='Resuming saved import',"
                           "ai_phase=NULL,ai_chars=0,error=NULL WHERE status IN ('normalizing','categorizing')")
                db.execute("UPDATE imports SET status='cancelled',stage='Import cancelled',finished_at=?,"
                           "ai_phase=NULL WHERE status='cancelling'", (_now(),))
                # A durable queue can outlive a model or backend switch; unfinished
                # jobs must run with the model the workspace is configured for now.
                db.execute("UPDATE imports SET model=? WHERE status IN ('queued','waiting_ai')",
                           (self.ai_model,))
            for allow_ai in (False, True):
                thread = threading.Thread(target=self._run, args=(allow_ai,),
                                          name="financial-ai" if allow_ai else "financial-imports", daemon=True)
                self._threads.append(thread)
                thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._state_lock:
            for control in self._controls.values():
                control.cancel()
        # A lease must never be released while a writer can still commit.
        for thread in self._threads:
            thread.join()
        self._threads.clear()
        if self._lease is not None:
            self._lease.close()
            self._lease = None

    def _folder(self, identifier: str) -> Path:
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise KeyError("Import not found.")
        return self.directory / identifier

    def _row(self, identifier: str):
        with self._connect() as db:
            row = db.execute("SELECT * FROM imports WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("Import not found.")
        return row

    def _public(self, row) -> dict:
        path = self._folder(row["id"]) / "transactions.csv"
        end = datetime.fromisoformat(row["finished_at"] or _now())
        def elapsed(start):
            return max(0, (end - datetime.fromisoformat(start)).total_seconds()) if start else 0
        return {key: row[key] for key in ("id", "name", "status", "stage", "completed", "total",
                                         "count", "error", "model", "created_at", "started_at",
                                         "ai_phase", "ai_chars")} | {
            "dismissed": bool(row["dismissed"]),
            "elapsed_seconds": elapsed(row["started_at"]),
            "stage_elapsed_seconds": elapsed(row["stage_started_at"]),
            "warnings": json.loads(row["warnings"]),
            "file": str(path.relative_to(self.root)) if row["status"] == "ready" else None,
        }

    def get(self, identifier: str) -> dict:
        return self._public(self._row(identifier))

    def list(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM imports ORDER BY created_at DESC, id").fetchall()
        return [self._public(row) for row in rows]

    def ready_files(self) -> list[Path]:
        return [self.root / row["file"] for row in self.list()
                if row["status"] == "ready" and (self.root / row["file"]).is_file()
                and not (self.root / row["file"]).is_symlink()]

    def active_file(self) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM preferences WHERE key='active_file'").fetchone()
        return row["value"] if row else None

    def select(self, file: str) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO preferences VALUES ('active_file',?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (file,))

    def submit(self, name: str, content: bytes) -> tuple[dict, bool]:
        from .import_formats import SUPPORTED_EXTENSIONS
        if (not name or name != Path(name).name or "\\" in name or len(name) > 255
                or any(ord(char) < 32 or ord(char) == 127 for char in name)):
            raise ValueError("Choose a filename without directory paths or control characters.")
        extension = Path(name).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise ValueError("Unsupported file type. Supported: " + ", ".join(SUPPORTED_EXTENSIONS))
        if not content or len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("Upload a non-empty statement of at most 25 MiB.")
        digest = hashlib.sha256(content).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM imports WHERE digest=?", (digest,)).fetchone()
            if existing is not None:
                return self._public(existing), False
            identifier = uuid.uuid4().hex
            folder = self._folder(identifier)
            folder.mkdir(mode=0o700)
            source = folder / ("source" + extension)
            try:
                with source.open("xb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                timestamp = _now()
                db.execute("INSERT INTO imports "
                           "(id,digest,name,extension,status,stage,model,created_at,updated_at,started_at,stage_started_at) "
                           "VALUES (?,?,?,?,'queued','Waiting to process',?,?,?,?,?)",
                           (identifier, digest, name, extension, self.ai_model,
                            timestamp, timestamp, timestamp, timestamp))
            except BaseException:
                source.unlink(missing_ok=True)
                folder.rmdir()
                raise
        self._wake.set()
        return self.get(identifier), True

    def retry(self, identifier: str) -> dict:
        with self._state_lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM imports WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError("Import not found.")
            if row["status"] not in {"failed", "cancelled"} or identifier in self._controls:
                raise ValueError("Only finished failed or cancelled imports can be retried.")
            timestamp = _now()
            db.execute("UPDATE imports SET status='queued',stage='Retrying saved import',error=NULL,model=?,"
                       "dismissed=0,started_at=?,stage_started_at=?,updated_at=?,finished_at=NULL,"
                       "ai_phase=NULL,ai_chars=0,completed=0,total=0 WHERE id=?",
                       (self.ai_model, timestamp, timestamp, timestamp, identifier))
        self._wake.set()
        return self.get(identifier)

    def cancel(self, identifier: str) -> dict:
        with self._state_lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM imports WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError("Import not found.")
            if row["status"] == "cancelled":
                return self.get(identifier)
            if row["status"] not in ACTIVE_STATES:
                raise ValueError("Only active imports can be cancelled.")
            control = self._controls.get(identifier)
            running = control is not None or row["status"] in {"normalizing", "categorizing", "cancelling"}
            timestamp = _now()
            db.execute("UPDATE imports SET status=?,stage=?,ai_phase=NULL,updated_at=?,finished_at=? WHERE id=?",
                       ("cancelling" if running else "cancelled",
                        "Stopping local processing" if running else "Import cancelled",
                        timestamp, None if running else timestamp, identifier))
            if control:
                control.cancel()
        return self.get(identifier)

    def dismiss(self, identifier: str, dismissed: bool) -> dict:
        with self._state_lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM imports WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError("Import not found.")
            if row["status"] not in TERMINAL_STATES:
                raise ValueError("Only finished imports can be dismissed.")
            db.execute("UPDATE imports SET dismissed=? WHERE id=?", (int(dismissed), identifier))
        return self.get(identifier)

    def delete(self, identifier: str) -> None:
        with self._state_lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM imports WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError("Import not found.")
            if row["status"] not in TERMINAL_STATES or identifier in self._controls:
                raise ValueError("Cancel the import and wait for it to stop before deleting it.")
            folder = self._folder(identifier)
            if folder.is_symlink():
                raise ValueError("Cannot delete a linked import directory.")
            if folder.exists():
                shutil.rmtree(folder)
            db.execute("DELETE FROM automatic_rules WHERE json_extract(decision,'$.source_import')=?", (identifier,))
            db.execute("DELETE FROM preferences WHERE key='active_file' AND value=?",
                       (str((folder / "transactions.csv").relative_to(self.root)),))
            db.execute("DELETE FROM imports WHERE id=?", (identifier,))

    def asset(self, identifier: str, kind: str) -> tuple[Path, str]:
        row = self._row(identifier)
        if kind == "source":
            path = self._folder(identifier) / ("source" + row["extension"])
            name = row["name"]
        elif kind == "normalized" and row["status"] == "ready":
            path = self._folder(identifier) / "transactions.csv"
            name = Path(row["name"]).stem + "-normalized.csv"
        else:
            raise KeyError("Normalized data is not ready.")
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(self.directory.resolve()):
            raise KeyError("Saved file is unavailable.")
        return path, name

    def is_managed(self, path: Path) -> bool:
        return path.resolve().is_relative_to(self.directory.resolve())

    def classify(self, path: Path, transactions, manual_rules) -> None:
        """Apply this dataset's saved AI decisions only below manual/type rules."""
        if not self.is_managed(path):
            return
        saved = json.loads((path.parent / "rules.json").read_text(encoding="utf-8"))
        decisions = {_rule_key(row): row for row in saved}
        rows = _automatic_candidates(transactions, manual_rules)
        assignments = {row["counterparty"].lower(): decisions.get(_rule_key(row)) for row in rows}
        for txn in transactions:
            decision = assignments.get(txn.counterparty.lower())
            if txn.category == Category.UNCATEGORIZED and decision:
                txn.category = Category(decision["category"])
                txn.label = decision["label"]

    def _check_active(self, db, identifier: str):
        row = db.execute("SELECT * FROM imports WHERE id=?", (identifier,)).fetchone()
        if self._stop.is_set() or row is None or row["status"] not in ACTIVE_STATES or row["status"] == "cancelling":
            raise InterruptedError("Import stopped.")
        return row

    def _update(self, identifier: str, **values) -> None:
        permitted = {"status", "stage", "completed", "total", "count", "error", "warnings", "ai_phase", "ai_chars"}
        if not values.keys() <= permitted:
            raise ValueError("Invalid import state update.")
        values["updated_at"] = _now()
        if "warnings" in values:
            values["warnings"] = json.dumps(values["warnings"], ensure_ascii=False)
        with self._state_lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._check_active(db, identifier)
            if "stage" in values and values["stage"] != row["stage"]:
                values["stage_started_at"] = values["updated_at"]
            db.execute("UPDATE imports SET " + ",".join(key + "=?" for key in values) + " WHERE id=?",
                       (*values.values(), identifier))

    def _settle(self, identifier: str, status: str, stage: str, error=None) -> None:
        with self._state_lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM imports WHERE id=?", (identifier,)).fetchone()
            if row is None or row["status"] in TERMINAL_STATES:
                return
            if row["status"] == "cancelling":
                status, stage, error = "cancelled", "Import cancelled", None
            elif self._stop.is_set():
                status, stage, error = "queued", "Paused until app restart", None
            if status == "waiting_ai" and row["status"] == "normalizing":
                db.execute("UPDATE imports SET completed=0,total=0 WHERE id=?", (identifier,))
            timestamp = _now()
            db.execute("UPDATE imports SET status=?,stage=?,error=?,ai_phase=?,ai_chars=0,"
                       "updated_at=?,stage_started_at=?,finished_at=? WHERE id=?",
                       (status, stage, error, "waiting" if status == "waiting_ai" else None,
                        timestamp, timestamp, timestamp if status in TERMINAL_STATES else None, identifier))
        self._wake.set()

    def _cancel_requested(self) -> None:
        # Requests may arrive through another API process sharing the workspace.
        with self._state_lock:
            controls = dict(self._controls)
        if not controls:
            return
        with self._connect() as db:
            requested = [row["id"] for row in db.execute("SELECT id FROM imports WHERE status='cancelling'")]
        for identifier in requested:
            if identifier in controls:
                controls[identifier].cancel()

    def _run(self, allow_ai: bool) -> None:
        queued_state = "waiting_ai" if allow_ai else "queued"
        while not self._stop.is_set():
            self._cancel_requested()
            with self._connect() as db:
                row = db.execute("SELECT id FROM imports WHERE status=? ORDER BY created_at,id LIMIT 1",
                                 (queued_state,)).fetchone()
            if row is None:
                self._wake.wait(timeout=0.25)
                self._wake.clear()
                continue
            try:
                self.process(row["id"], allow_ai=allow_ai, expected_status=queued_state)
            except ai_rules.InferenceRequired:
                self._settle(row["id"], "waiting_ai", "Waiting for local AI; other imports can continue")
            except InterruptedError:
                self._settle(row["id"], "cancelled", "Import cancelled")
            except Exception as exc:
                self._settle(row["id"], "failed", "Import needs attention", str(exc))

    def process(self, identifier: str, *, allow_ai: bool = True, expected_status: str | None = None) -> None:
        """Run deterministic work independently; serialize only actual model requests."""
        last_phase, last_progress = None, 0.0
        def model_progress(phase, chars):
            nonlocal last_phase, last_progress
            now = time.monotonic()
            if phase != last_phase or now - last_progress >= 1:
                self._update(identifier, ai_phase=phase, ai_chars=chars)
                last_phase, last_progress = phase, now
        control = ai_rules.InferenceControl(self.generation_lock, allow_ai=allow_ai, progress=model_progress)
        with self._state_lock:
            row = self._row(identifier)
            if (row["status"] in TERMINAL_STATES or identifier in self._controls
                    or (expected_status is not None and row["status"] != expected_status)):
                return
            self._controls[identifier] = control
        try:
            with ai_rules.inference_session(control):
                control.check_cancelled()
                self._process(identifier)
        finally:
            with self._state_lock:
                del self._controls[identifier]

    def _process(self, identifier: str) -> None:
        from .import_formats import normalize_file
        row = self._row(identifier)
        if row["status"] == "ready":
            return
        folder = self._folder(identifier)
        csv_path = folder / "transactions.csv"
        metadata_path = folder / "normalization.json"
        self._update(identifier, status="normalizing", stage="Reading and normalizing statement", error=None)
        if csv_path.is_file() and metadata_path.is_file():
            transactions = load_transactions(csv_path)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        else:
            def progress(stage, completed, total):
                if self._stop.is_set():
                    raise InterruptedError("Import paused.")
                self._update(identifier, stage=stage, completed=completed, total=total)
            result = normalize_file(folder / ("source" + row["extension"]), ai_url=self.ai_url,
                                    ai_model=row["model"], progress=progress)
            if not result.transactions or len(result.transactions) != len(result.provenance):
                raise ValueError("Normalization did not provide a source reference for every transaction.")
            ordered = sorted(zip(result.transactions, result.provenance), key=lambda item: (item[0].date, item[0].txn_id))
            transactions = [item[0] for item in ordered]
            metadata = {"format": result.format, "mapping": result.mapping, "configured_model": row["model"],
                        "warnings": result.warnings, "provenance": [item[1] for item in ordered]}
            temporary = folder / ".transactions.pending"
            try:
                write_normalized(temporary, transactions)
                with temporary.open("rb") as stream:
                    os.fsync(stream.fileno())
                temporary.replace(csv_path)
            finally:
                temporary.unlink(missing_ok=True)
            _write_json(metadata_path, metadata)
        rules = ai_rules.effective_rules(self.base_rules, self.approved_rules)
        classify_all(transactions, rules)
        candidates = _automatic_candidates(transactions, rules)
        decisions = {}
        with self._connect() as db:
            for candidate in candidates:
                key = _rule_key(candidate)
                cached = db.execute("SELECT decision FROM automatic_rules WHERE signature=? AND model=?",
                                    (key, row["model"])).fetchone()
                if cached:
                    decisions[key] = {**json.loads(cached["decision"]), **candidate,
                                      "label": candidate["counterparty"]}
        pending = [candidate for candidate in candidates if _rule_key(candidate) not in decisions]
        self._update(identifier, status="categorizing", stage="Automatically categorizing counterparties",
                     completed=len(decisions), total=len(candidates), count=len(transactions),
                     warnings=metadata["warnings"])
        for offset in range(0, len(pending), AUTO_BATCH_SIZE):
            if self._stop.is_set():
                raise InterruptedError("Import paused.")
            batch = pending[offset:offset + AUTO_BATCH_SIZE]
            proposed = ai_rules.suggestions(self.ai_url, row["model"], batch)
            with self._state_lock, self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._check_active(db, identifier)
                for decision in proposed:
                    # Preserve source identities in counterparty charts; the model's
                    # category explanation must not merge unrelated merchants by label.
                    decision = {**decision, "suggested_label": decision["label"],
                                "suggested_category": decision["category"], "suggested_reason": decision["reason"],
                                "source_import": identifier, "source_count": decision["count"],
                                "model": row["model"], "label": decision["counterparty"]}
                    # Names and bank-transfer descriptions do not establish asset ownership.
                    if decision["category"] in {"transfer", "investment"}:
                        decision = {**decision, "category": "uncategorized",
                                    "reason": "Account ownership needs confirmation before treating this as an asset movement."}
                    key = _rule_key(decision)
                    decisions[key] = decision
                    db.execute("INSERT INTO automatic_rules VALUES (?,?,?) ON CONFLICT(signature,model) "
                               "DO UPDATE SET decision=excluded.decision",
                               (key, row["model"], json.dumps(decision, ensure_ascii=False)))
            self._update(identifier, completed=len(decisions))
        saved_rules = [decisions[_rule_key(candidate)] for candidate in candidates]
        _write_json(folder / "rules.json", saved_rules)
        warnings = list(metadata["warnings"])
        warnings.append("Balances show cumulative cash flow from these transactions, starting at zero; no actual opening balance was inferred.")
        if saved_rules:
            warnings.append("Automatic AI categories are provisional. Review important transactions; manual rules take priority.")
        unresolved = sum(decision["category"] == "uncategorized" for decision in saved_rules)
        if unresolved:
            warnings.append(f"{unresolved} counterparties remain uncategorized because their purpose or account ownership needs confirmation.")
        with self._state_lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_active(db, identifier)
            timestamp = _now()
            db.execute("UPDATE imports SET status='ready',stage='Saved and ready',error=NULL,"
                       "completed=?,total=?,count=?,warnings=?,updated_at=?,finished_at=?,"
                       "stage_started_at=?,ai_phase=NULL WHERE id=?",
                       (len(candidates), len(candidates), len(transactions),
                        json.dumps(warnings, ensure_ascii=False), timestamp, timestamp, timestamp, identifier))
            # Completing another upload must not steal an explicitly selected dashboard.
            db.execute("INSERT OR IGNORE INTO preferences VALUES ('active_file',?)",
                       (str(csv_path.relative_to(self.root)),))
