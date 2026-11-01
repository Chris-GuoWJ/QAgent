"""Offline CLI for the same service used by natural-language tools."""

import json
from uuid import uuid4

from ...bootstrap import load_runtime
from .parameters import QuantumXSettings
from .service import QuantumXService


def execute(args, store, emit):
    runtime = load_runtime(args.runtime)
    if runtime.quantumx is None:
        raise ValueError("运行配置未启用 quantumx")
    service = QuantumXService(store, QuantumXSettings.model_validate(runtime.quantumx))
    try:
        if args.action == "describe":
            result = service.describe()
        elif args.action == "list":
            result = service.list_results()
        elif args.action == "result":
            result = service.public_result(args.identifier)
        elif args.action == "stop":
            result = service.stop(args.identifier)
        elif args.action == "compare":
            result = service.compare(args.identifier, args.second)
        elif args.action == "run":
            result = service.run_sync(args.identifier, json.loads(args.params), args.session)
        else:
            session_id = args.session or uuid4().hex
            results, reference = [], None
            for name in ("readout_centers", "pi_amplitude", "ramsey_frequency"):
                params = {"reference_experiment_id": reference} if reference else {}
                item = service.run_sync(name, params, session_id)
                results.append(item)
                if item["status"] != "completed" or not item["quality"]["accepted"]:
                    break
                reference = item["experiment_id"]
            result = {"session_id": session_id, "experiments": results, "data_origin": "simulated"}
        emit(result)
        if args.action == "demo" and (len(results) != 3 or not all(r["quality"]["accepted"] for r in results)):
            return 2
        reports = result.get("experiments", [result]) if args.action == "demo" else [result]
        return 2 if args.action in {"run", "demo"} and any(r.get("status") != "completed" for r in reports) else 0
    finally:
        service.pause()
        service.wait(timeout=2)
