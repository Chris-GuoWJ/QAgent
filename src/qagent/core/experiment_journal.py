"""Persistent experiment identity, recovery, and shared query operations."""

import hashlib
import json
import time
from uuid import uuid4


ACTIVE_STATES = {"queued", "submitted", "starting", "running", "cancelling", "unknown"}


class ExperimentJournal:
    def _init_journal(self):
        with self.store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS quantumx_hardware_experiments (
                    id TEXT PRIMARY KEY, operation_key TEXT UNIQUE NOT NULL,
                    session_id TEXT NOT NULL, identity TEXT NOT NULL, signature TEXT NOT NULL,
                    cost INTEGER NOT NULL, stopped INTEGER NOT NULL DEFAULT 0, report TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS quantumx_hardware_sessions (id TEXT PRIMARY KEY, identity TEXT NOT NULL);
            """)

    @staticmethod
    def _key(session_id, call_id):
        return hashlib.sha256(f"{session_id}:{call_id}".encode()).hexdigest()

    def bind_session(self, session_id):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT identity FROM quantumx_hardware_sessions WHERE id=?", (session_id,)).fetchone()
            if row and row[0] != self.identity:
                raise ValueError("QuantumX 配置或源码已变化，请使用原配置或新建会话")
            db.execute("INSERT OR IGNORE INTO quantumx_hardware_sessions VALUES(?,?)", (session_id, self.identity))

    def get(self, experiment_id):
        with self.store.connect() as db:
            row = db.execute("SELECT identity,report FROM quantumx_hardware_experiments WHERE id=?", (experiment_id,)).fetchone()
        if not row or row[0] != self.identity:
            raise ValueError("找不到当前 QuantumX 配置的实验编号")
        return json.loads(row[1])

    def list_results(self, session_id=None):
        with self.store.connect() as db:
            rows = db.execute("SELECT id FROM quantumx_hardware_experiments WHERE identity=? "
                              "AND (? IS NULL OR session_id=?) ORDER BY rowid DESC LIMIT 20",
                              (self.identity, session_id, session_id)).fetchall()
        return {"experiments": [self.public_result(row[0]) for row in rows]}

    def recover_operation(self, session_id, call_id):
        with self.store.connect() as db:
            row = db.execute("SELECT id FROM quantumx_hardware_experiments WHERE operation_key=? AND identity=?",
                             (self._key(session_id, call_id), self.identity)).fetchone()
        return ({"recovered_receipt": True, **self.public_result(row[0])} if row else
                {"interrupted": True, "message": "未找到已保存回执，未自动重放实验；请先查询已有记录。"})

    def is_active(self, experiment_id):
        worker = self._workers.get(experiment_id)
        return bool(worker and worker.is_alive())

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        for worker in list(self._workers.values()):
            worker.join(None if deadline is None else max(0, deadline - time.monotonic()))

    def compare(self, first, second):
        a, b = self.public_result(first), self.public_result(second)
        if a["n"] != b["n"] or a["experiment_type"] != b["experiment_type"]:
            raise ValueError("只能比较同一目标、同一种实验")
        if a["status"] != "completed" or b["status"] != "completed":
            raise ValueError("只能比较已完成的实验")
        delta = {k: b["metrics"][k] - v for k, v in a["metrics"].items()
                 if type(v) in (float, int) and type(b["metrics"].get(k)) in (float, int)}
        return {"first": a, "second": b, "metric_delta_second_minus_first": delta}

    def run_sync(self, name, parameters=None, session_id=None):
        receipt = self.start(name, parameters or {}, session_id or uuid4().hex, uuid4().hex)
        self.wait()
        return self.public_result(receipt["experiment_id"])
