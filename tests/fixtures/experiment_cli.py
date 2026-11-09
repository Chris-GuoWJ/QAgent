"""Standalone fixture for subprocess protocol tests. It never uses devices."""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--experiment-dir", type=Path)
    args = parser.parse_args()
    assert "DEEPSEEK_API_KEY" not in os.environ
    version = {"protocol_version": "1"}
    if args.command == "describe":
        print(json.dumps({**version, "data_origin": "test", "experiments": [
            {"name": "readout_centers", "defaults": {"shots": 20}},
            {"name": "pi_amplitude", "defaults": {"shots": 20}},
        ]}))
        return 0

    directory = args.request.parent if args.request else args.experiment_dir
    identity = {**version, "experiment_id": directory.name}
    if args.command == "stop":
        (directory / "stop.request").touch()
        print(json.dumps({**identity, "status": "cancelling", "stop_requested": True}))
        return 0
    if args.command == "status":
        print((directory / "status.json").read_text(encoding="utf-8"))
        return 0

    request = json.loads(args.request.read_text(encoding="utf-8"))
    shots = request["parameters"]["shots"]
    points = request["parameters"].get("points", 1)
    groups = 2 if request["experiment"] == "readout_centers" else points
    totals = {"scan_points_total": points, "iq_groups_total": groups}
    if args.command == "validate":
        print(json.dumps({**identity, **totals, "planned_measurements": groups * shots}))
        return 0

    seq = 0
    counts = {"scan_points_done": 0, "iq_groups_done": 0, "shots_done": 0}

    def emit(event, status, phase=None, *, stdout=True):
        nonlocal seq
        seq += 1
        values = {**identity, **totals, **counts, "seq": seq}
        if phase:
            values["phase"] = phase
        record = {**values, "event": event, "timestamp": datetime.now(timezone.utc).isoformat()}
        with (directory / "events.jsonl").open("a", encoding="utf-8") as events:
            events.write(json.dumps(record) + "\n")
        write_json(directory / "status.json", {**values, "status": status})
        if stdout:
            print(json.dumps(record), flush=True)

    (directory / "fixture-runs").write_text("one", encoding="utf-8")
    write_json(directory / "effective_request.json", request)
    emit("started", "running", "initializing", stdout=not Path("skip-start").exists())
    sys.stderr.write("fixture diagnostics\n" * 16000)
    sys.stderr.flush()
    raw = directory / "raw"
    raw.mkdir()
    raw_file = raw / "iq.json"
    write_json(raw_file, [[0, 0]] * shots)
    counts.update(iq_groups_done=1, shots_done=shots)
    emit("progress", "running", "acquiring")
    deadline = time.monotonic() + 10
    while Path("hold").exists() and not (directory / "stop.request").exists():
        if time.monotonic() >= deadline:
            emit("failed", "failed")
            return 1
        time.sleep(0.025)

    artifacts = [{"role": "raw_iq", "format": "json", "path": str(raw_file)}]
    cancelled = (directory / "stop.request").exists()
    status = "cancelled" if cancelled else "completed"
    if not cancelled:
        counts.update(scan_points_done=points, iq_groups_done=groups, shots_done=groups * shots)
        if not Path("no-plot").exists():
            plots = directory / "plots"
            plots.mkdir()
            plot = plots / "fit.svg"
            plot.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
            artifacts.append({"role": "plot", "format": "svg", "path": str(plot)})
    if not Path("missing-result").exists():
        write_json(directory / "result.json", {
            **identity, **counts, "operation_key": request["operation_key"],
            "experiment": request["experiment"], "status": status,
            "artifacts": artifacts, "metrics": {"sample": 1.0},
            "quality": {"accepted": not cancelled, "independently_verified": False},
        })
    emit(status, status, stdout=not Path("omit-terminal-event").exists())
    return 3 if cancelled else 0


if __name__ == "__main__":
    sys.exit(main())
