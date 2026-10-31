"""Business journal and artifacts, independent of graph checkpoint internals."""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .schemas import RunState


class RunBusy(RuntimeError):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "journal.sqlite"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, stopped INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    timestamp REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, state: RunState):
        with self.connect() as db:
            db.execute("INSERT INTO runs(id,state) VALUES(?,?)", (state.run_id, state.model_dump_json()))
            self._event(db, state.run_id, "run_created", {"stage": state.stage})

    def load(self, run_id: str) -> RunState:
        with self.connect() as db:
            row = db.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown run: {run_id}")
        return RunState.model_validate_json(row[0])

    def save(self, state: RunState, kind: str, payload: dict | None = None):
        with self.connect() as db:
            db.execute("UPDATE runs SET state=? WHERE id=?", (state.model_dump_json(), state.run_id))
            self._event(db, state.run_id, kind, payload or {"stage": state.stage})

    def begin_submission(self, state: RunState) -> bool:
        """Linearize submission start against stop requests before external effects."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT stopped FROM runs WHERE id=?", (state.run_id,)).fetchone()
            if row[0]:
                return False
            state.current.attempted = True
            db.execute("UPDATE runs SET state=? WHERE id=?", (state.model_dump_json(), state.run_id))
            self._event(db, state.run_id, "submission_started", {"request_id": state.current.request_id})
        return True

    def request_stop(self, run_id: str):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
                raise KeyError(f"Unknown run: {run_id}")
            db.execute("UPDATE runs SET stopped=1 WHERE id=?", (run_id,))
            self._event(db, run_id, "stop_requested", {})

    def stopped(self, run_id: str) -> bool:
        with self.connect() as db:
            return bool(db.execute("SELECT stopped FROM runs WHERE id=?", (run_id,)).fetchone()[0])

    @staticmethod
    def _event(db, run_id, kind, payload):
        db.execute("INSERT INTO events(run_id,timestamp,kind,payload) VALUES(?,?,?,?)",
                   (run_id, time.time(), kind, json.dumps(payload, ensure_ascii=False, allow_nan=False)))

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT seq,timestamp,kind,payload FROM events WHERE run_id=? AND seq>? ORDER BY seq",
                              (run_id, after)).fetchall()
        return [{"seq": n, "run_id": run_id, "timestamp": ts, "event": kind, **json.loads(payload)}
                for n, ts, kind, payload in rows]

    def list_runs(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT state,stopped FROM runs ORDER BY rowid DESC").fetchall()
        return [{"run_id": (s := RunState.model_validate_json(raw)).run_id,
                 "stage": s.stage, "outcome": s.outcome, "stop_requested": bool(stop)} for raw, stop in rows]

    def write_artifact(self, run_id: str, name: str, data: dict) -> str:
        directory = self.root / run_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return str(path.relative_to(self.root))

    def read_artifact(self, reference: str) -> dict:
        path = (self.root / reference).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("artifact reference must stay inside run storage")
        return json.loads(path.read_text(encoding="utf-8"))

    @contextmanager
    def run_lock(self, run_id: str):
        """OS lock is released automatically if the process crashes."""
        # Hash input so user-provided run IDs cannot escape storage.
        from hashlib import sha256

        path = self.root / (sha256(run_id.encode()).hexdigest() + ".lock")
        handle = path.open("a+b")
        try:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RunBusy(f"Run {run_id} already has an active executor") from exc
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
