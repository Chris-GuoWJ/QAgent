import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from langgraph.checkpoint.sqlite import SqliteSaver

from qagent.bootstrap import RuntimeSettings, create_controller
from qagent.graph import drive
from qagent.storage import Store


ROOT = Path(__file__).parents[1]


def test_graph_checkpoints_and_resume(make_controller, task):
    controller = make_controller()
    run_id = controller.create(task).run_id
    first_events = []
    first = drive(controller, run_id, max_steps=2, poll_interval=0, on_event=first_events.append)
    assert first["stage"] == "wait" and first["outcome"] is None
    with SqliteSaver.from_conn_string(str(controller.store.root / "checkpoints.sqlite")) as saver:
        checkpoint = saver.get_tuple({"configurable": {"thread_id": run_id}})
        assert checkpoint.checkpoint["channel_values"]["run_id"] == run_id
    rebuilt = make_controller()
    resumed_events = []
    result = drive(rebuilt, run_id, poll_interval=0, on_event=resumed_events.append)
    assert result["outcome"] == "success"
    assert min(e["seq"] for e in resumed_events) > max(e["seq"] for e in first_events)
    with sqlite3.connect(rebuilt.backend.path) as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == result["budget"]["experiments"]


def invoke(*args):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, "-B", "-m", "qagent", *map(str, args)], cwd=ROOT,
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_cli_cross_process_resume_and_status(tmp_path):
    storage = tmp_path / "runs"
    first = invoke("--storage", storage, "start", "--task", ROOT / "configs/task.spectroscopy.json",
                   "--max-steps", "2", "--quiet")
    assert first.returncode == 0, first.stderr
    report = json.loads(first.stdout)
    run_id = report["run_id"]
    assert report["stage"] == "wait"
    status = invoke("--storage", storage, "status", run_id)
    assert json.loads(status.stdout)["current"]["job_id"]
    resumed = invoke("--storage", storage, "resume", run_id, "--quiet")
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(resumed.stdout)["outcome"] == "success"
    assert Path(json.loads(resumed.stdout)["report_path"]).is_file()


def test_cli_stop_is_persisted_without_model_credentials(tmp_path):
    storage = tmp_path / "runs"
    first = invoke("--storage", storage, "start", "--task", ROOT / "configs/task.spectroscopy.json",
                   "--max-steps", "1", "--quiet")
    assert first.returncode == 0, first.stderr
    run_id = json.loads(first.stdout)["run_id"]
    stopped = invoke("--storage", storage, "stop", run_id)
    assert stopped.returncode == 0
    state = json.loads(invoke("--storage", storage, "status", run_id).stdout)
    assert state["stop_requested"]
    result = json.loads(invoke("--storage", storage, "resume", run_id, "--quiet").stdout)
    assert result["outcome"] == "stopped"
    assert result["budget"]["used_measurements"] == 0


def test_cli_unknown_run_and_invalid_step_limit(tmp_path):
    missing = invoke("--storage", tmp_path, "status", "unknown")
    assert missing.returncode == 2 and "Unknown run" in missing.stderr
    invalid = invoke("--storage", tmp_path, "start", "--task", ROOT / "configs/task.spectroscopy.json", "--max-steps", "0")
    assert invalid.returncode == 2
    assert not Store(tmp_path).list_runs()


def test_bootstrap_uses_external_factory_without_core_branch(tmp_path, monkeypatch):
    import types
    from conftest import ReplayBackend

    module = types.ModuleType("external_lab_adapter")
    module.create = lambda options, root: ReplayBackend()
    monkeypatch.setitem(sys.modules, "external_lab_adapter", module)
    controller = create_controller(Store(tmp_path), RuntimeSettings(backend_factory="external_lab_adapter:create"))
    assert controller.backend.identity == "replay-fixture-v1"
