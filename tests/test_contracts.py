import ast
import importlib.abc
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from conftest import ReplayBackend
from qagent.backend.simulator import SimulationSettings, SimulatorBackend
from qagent.schemas import Decision, ExperimentRequest, Measurement, ScanConfig


@pytest.fixture(params=["persistent", "replay"])
def backend(request, tmp_path):
    if request.param == "replay":
        return ReplayBackend()
    return SimulatorBackend(tmp_path / "backend.sqlite", SimulationSettings(duration_seconds=0))


def test_backend_contract(backend):
    request = ExperimentRequest(request_id="request-1", qubit="Q0", experiment_type="spectroscopy",
        purpose="search", config=ScanConfig(start_hz=4.8e9, stop_hz=5.2e9, points=81, shots_per_point=256))
    assert backend.lookup(request.request_id).state == "absent"
    job = backend.submit(request)
    assert backend.submit(request) == job
    assert backend.lookup(request.request_id).job_id == job
    states = [backend.status(job).state for _ in range(3)]
    assert states[-1] == "completed"
    measurement = backend.fetch(job)
    assert measurement.request_id == request.request_id
    assert measurement.config == request.config
    assert Measurement.model_validate_json(measurement.model_dump_json()) == measurement
    changed = request.model_copy(update={"config": request.config.model_copy(update={"shots_per_point": 512})})
    with pytest.raises(ValueError):
        backend.submit(changed)


@pytest.mark.parametrize("changes", [{"start_hz": float("nan")}, {"stop_hz": float("inf")},
    {"points": True}, {"points": 1}, {"start_hz": 6e9}])
def test_scan_rejects_invalid_values(changes):
    values = dict(start_hz=4.8e9, stop_hz=5.2e9, points=81, shots_per_point=256)
    with pytest.raises(ValidationError):
        ScanConfig(**(values | changes))


def test_task_decision_rejects_simulation_fields():
    with pytest.raises(ValidationError):
        Decision(action="finish", reason="stop", true_frequency_hz=5e9)


def test_core_imports_without_simulation_or_provider_modules():
    # A fresh interpreter is essential: an already-imported simulator would mask coupling.
    script = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"qagent.backend.simulator", "langchain_deepseek"}:
            raise AssertionError("forbidden adapter import: " + fullname)
sys.meta_path.insert(0, Block())
import qagent, qagent.schemas, qagent.controller, qagent.policy, qagent.analysis
import qagent.state, qagent.storage, qagent.graph
import qagent.chat, qagent.chat_tools, qagent.chat_history, qagent.terminal
print("isolated")
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run([sys.executable, "-B", "-c", script], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "isolated" in result.stdout


def test_core_source_has_no_concrete_adapter_imports():
    root = Path(__file__).parents[1] / "src" / "qagent"
    for name in ["controller.py", "policy.py", "analysis.py", "schemas.py", "graph.py", "state.py", "storage.py"]:
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "simulator" not in (node.module or "")
            if isinstance(node, ast.Import):
                assert not any("simulator" in alias.name for alias in node.names)
