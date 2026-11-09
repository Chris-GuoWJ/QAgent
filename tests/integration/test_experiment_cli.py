"""Exercise the external experiment adapter through real offline subprocesses."""

import json
import shutil
import sys
import threading
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from qagent.agent.history import ChatHistory
from qagent.agent.loop import ChatAgent
from qagent.agent.session import ChatOperations
from qagent.app import create_service, create_store, load_runtime
from qagent.interfaces.cli import main
from qagent.interfaces.terminal import format_event
from tests.integration.test_agent import ScriptedModel, call


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    worker = tmp_path / "experiment system"
    worker.mkdir()
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "experiment_cli.py"
    shutil.copyfile(fixture, worker / "test worker.py")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({
        "state_dir": "journal", "result_dir": "agent-reports", "input_archive_dir": "input-history",
        "experiment_cli": {
            "python_executable": sys.executable, "entrypoint": "experiment system/test worker.py",
            "working_directory": "experiment system", "output_root": "runs",
        },
    }), encoding="utf-8")
    return path, worker


def open_service(path, on_event=None):
    settings = load_runtime(path)
    return create_service(create_store(None, settings), settings, on_event=on_event)


def test_public_cli_runs_and_reports_artifacts(runtime, capsys, monkeypatch):
    path, worker = runtime
    monkeypatch.setitem(sys.modules, "quantumx", None)
    assert main(["--runtime", str(path), "check"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["backend"] == "experiment_cli" and not checked["device_connected"]
    (worker / "omit-terminal-event").touch()
    assert main(["--runtime", str(path), "run", "readout_centers"]) == 0
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["status"] == "completed"
    assert report["iq_acquisitions"] == 2 and report["actual_measurements"] == 40
    assert Path(report["result_ref"]).is_file()
    assert Path(report["raw_data_dir"], "iq.json").is_file()
    assert Path(report["plot_files"][0]).is_file()
    assert "扫描点 0/1，IQ 1/2 组" in output.err
    assert "原始数据" in output.err and "图像" in output.err


def test_streams_progress_and_can_stop_from_reopened_service(runtime):
    path, worker = runtime
    (worker / "hold").touch()
    (worker / "skip-start").touch()
    progress = threading.Event()
    messages = []

    def on_event(event):
        messages.append(format_event(event))
        if event["event"] == "experiment_progress":
            progress.set()

    service = open_service(path, on_event)
    history = ChatHistory(service.store, service.identity)
    model = ScriptedModel([call("measure_readout_centers"), AIMessage(content="已启动读出中心实验。")])
    agent = ChatAgent(model, ChatOperations(service, history))
    assert agent.respond("计算 q1 的读出中心") == "已启动读出中心实验。"
    receipt = next(json.loads(message.content) for message in history.messages() if isinstance(message, ToolMessage))
    experiment_id = receipt["experiment_id"]
    session = history.session_id
    try:
        assert receipt["parameters"]["shots"] == 20
        assert progress.wait(5)
        assert service.is_active(experiment_id)
        assert any("实验开始" in item for item in messages)
        reader = open_service(path)
        assert reader.public_result(experiment_id)["iq_acquisitions"] == 1
        assert reader.recover_operation(session, "call")["experiment_id"] == experiment_id
        assert reader.start("readout_centers", {}, session, "call")["experiment_id"] == experiment_id
        with pytest.raises(ValueError, match="未结束"):
            reader.start("readout_centers", {}, session, "different")
        assert reader.stop(experiment_id)["stop_requested"]
        service.wait(timeout=5)
        result = reader.public_result(experiment_id)
        assert result["status"] == "cancelled"
        assert result["iq_acquisitions"] == 1 and Path(result["result_ref"]).is_file()
        assert len(reader.list_results()["experiments"]) == 1
        assert len(model.inputs) == 2
    finally:
        service.pause()
        service.wait(timeout=5)


def test_missing_manifest_is_not_treated_as_success_or_replayed(runtime):
    path, worker = runtime
    (worker / "missing-result").touch()
    service = open_service(path)
    result = service.run_sync("readout_centers", {}, "session")
    assert result["status"] == "unknown"
    assert not result.get("result_ref")
    with pytest.raises(ValueError, match="待核对"):
        open_service(path).start("readout_centers", {}, "session", "new")


def test_candidate_reference_is_passed_in_next_request(runtime):
    path, worker = runtime
    (worker / "no-plot").touch()
    service = open_service(path)
    first = service.run_sync("readout_centers", {"shots": 20}, "session")
    assert first["status"] == "completed"
    assert first["warnings"] == ["实验结果未提供图像"]
    second = service.run_sync("pi_amplitude", {"points": 7, "reference_experiment_id": first["experiment_id"]},
                              "session")
    assert second["status"] == "completed"
    request = json.loads(Path(second["request_path"]).read_text(encoding="utf-8"))
    assert request["input_refs"] == {"candidate_result": first["result_ref"]}
    assert request["parent_experiment_id"] == first["experiment_id"]
    assert request["iteration_index"] == 1


def test_function_tools_follow_executor_capabilities_and_skill_is_read_only(runtime):
    path, worker = runtime
    (worker / "extended-catalog").touch()
    service = open_service(path)
    history = ChatHistory(service.store, service.identity)
    model = ScriptedModel([
        call("get_calibration_skill", {"method": "calCavityClear"}, identifier="skill"),
        AIMessage(content="已说明腔清空原理。"),
        call("calT1", {"points": 7, "delay_stop_s": 12e-6}, identifier="measurement"),
        AIMessage(content="已提交 T1 测量。"),
    ])
    agent = ChatAgent(model, ChatOperations(service, history))
    assert {"calc_readout_centers", "pituner", "calT1", "calXYZDelay"} <= agent.tools.keys()
    assert not {"freqtuner", "calCavityClear", "measure_readout_centers"} & agent.tools.keys()
    assert set(agent.tools["calXYZDelay"].args_schema["required"]) == {"z_amp", "z_duration_s"}
    agent.respond("解释腔清空")
    skill = json.loads(next(message.content for message in history.messages() if isinstance(message, ToolMessage)))
    assert not skill["runnable"] and "Ramsey" in skill["guide"]
    assert service.list_results()["experiments"] == []

    agent.respond("测量 T1，扫 7 个点到 12 微秒")
    service.wait()
    report = service.list_results()["experiments"][0]
    assert report["status"] == "completed" and report["method"] == "calT1"
    assert report["reserved_measurements"] == 140 and report["metric_units"] == {"sample": "s"}
    request = json.loads(Path(report["request_path"]).read_text(encoding="utf-8"))
    assert request["experiment"] == "t1"
    assert request["parameters"]["delay_stop_s"] == 12e-6


def test_cli_uses_preflight_measurements_for_multigroup_budget(runtime, capsys):
    path, worker = runtime
    (worker / "extended-catalog").touch()
    config = json.loads(path.read_text(encoding="utf-8"))
    config["experiment_cli"]["max_measurements"] = 41
    path.write_text(json.dumps(config), encoding="utf-8")
    assert main(["--runtime", str(path), "run", "calXYZDelay", "--params",
                 '{"shots":10,"points":7,"z_amp":0.1,"z_duration_s":1e-8}']) == 2
    assert "实际计划将超过" in capsys.readouterr().err
    assert not list((path.parent / "runs").glob("*/fixture-runs"))
