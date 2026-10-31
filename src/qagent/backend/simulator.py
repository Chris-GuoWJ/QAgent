"""Persistent test adapter. Private scenario settings never leave this module."""

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import numpy as np
from pydantic import Field

from ..schemas import Capabilities, ExperimentRequest, JobStatus, LookupResult, Measurement, Model
from .base import BackendError, SubmissionUnknown


class SimulationSettings(Model):
    true_frequency_hz: float = Field(default=5.0317e9, gt=0)
    linewidth_hz: float = Field(default=5e6, gt=0)
    noise_std: float = Field(default=0.2, ge=0)
    contrast: float = Field(default=0.65, ge=0, le=0.8)
    duration_seconds: float = Field(default=0.05, ge=0)
    seed: int = 42
    fail_jobs: bool = False
    ambiguous_submit: bool = False
    unknown_lookup: bool = False


class SimulatorBackend:
    def __init__(self, path: Path, settings: SimulationSettings):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._settings = settings
        fingerprint = hashlib.sha256(settings.model_dump_json().encode()).hexdigest()
        self._identity = "backend:" + hashlib.sha256((str(self.path) + fingerprint).encode()).hexdigest()
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    request_id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL,
                    request TEXT NOT NULL, created REAL NOT NULL, result TEXT NOT NULL
                );
            """)
            row = db.execute("SELECT value FROM metadata WHERE key='settings'").fetchone()
            if row and row[0] != fingerprint:
                raise ValueError("Backend database belongs to a different scenario; use another storage root")
            db.execute("INSERT OR IGNORE INTO metadata VALUES('settings',?)", (fingerprint,))

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @property
    def identity(self) -> str:
        return self._identity

    def capabilities(self):
        return Capabilities(lower_hz=4e9, upper_hz=6e9, request_lookup=True, submission_deduplication=True)

    @staticmethod
    def _request_json(request):
        return request.model_dump_json(exclude={"attempted", "job_id", "charged"})

    def submit(self, request: ExperimentRequest) -> str:
        serialized = self._request_json(request)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT job_id,request FROM jobs WHERE request_id=?", (request.request_id,)).fetchone()
            if old:
                if old[1] != serialized:
                    raise ValueError("request_id reused with different parameters")
                return old[0]
            job_id = "job_" + uuid4().hex
            result = self._generate(request, job_id)
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?)",
                       (request.request_id, job_id, serialized, time.time(), result.model_dump_json()))
        if self._settings.ambiguous_submit:
            raise SubmissionUnknown("Response lost after request acceptance")
        return job_id

    def lookup(self, request_id: str):
        if self._settings.unknown_lookup:
            return LookupResult(state="unknown")
        with self._connect() as db:
            row = db.execute("SELECT job_id FROM jobs WHERE request_id=?", (request_id,)).fetchone()
        return LookupResult(state="found", job_id=row[0]) if row else LookupResult(state="absent")

    def status(self, job_id: str):
        with self._connect() as db:
            row = db.execute("SELECT created FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return JobStatus(state="unknown", message="Unknown job")
        elapsed = time.time() - row[0]
        duration = self._settings.duration_seconds
        if elapsed < duration / 3:
            return JobStatus(state="queued")
        if elapsed < duration:
            return JobStatus(state="running")
        return JobStatus(state="failed" if self._settings.fail_jobs else "completed")

    def fetch(self, job_id: str):
        if self.status(job_id).state != "completed":
            raise BackendError("Result is not available")
        with self._connect() as db:
            row = db.execute("SELECT result FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return Measurement.model_validate_json(row[0])

    def _generate(self, request, job_id):
        config = request.config
        seed = int.from_bytes(hashlib.sha256(f"{self._settings.seed}:{request.request_id}".encode()).digest()[:8])
        rng = np.random.default_rng(seed)
        x = np.linspace(config.start_hz, config.stop_hz, config.points)
        delta = x - self._settings.true_frequency_hz
        width = self._settings.linewidth_hz
        signal = 0.1 + self._settings.contrast * width**2 / (delta**2 + width**2)
        noise = rng.normal(0, self._settings.noise_std / np.sqrt(config.shots_per_point), config.points)
        return Measurement(request_id=request.request_id, job_id=job_id, qubit=request.qubit,
            experiment_type=request.experiment_type, config=config,
            frequencies=x.tolist(), values=np.clip(signal + noise, 0, 1).tolist())


def create_backend(options: dict, root: Path):
    """Composition hook; private options stay at the adapter boundary."""
    return SimulatorBackend(root / "backend.sqlite", SimulationSettings.model_validate(options))
