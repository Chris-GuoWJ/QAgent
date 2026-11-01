"""CLI uses the same controller for fixed and DeepSeek decision policies."""

import argparse
import json
import sys
from pathlib import Path

from .schemas import CalibrationTask
from .config import read_json_config
from .storage import Store


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="qagent", description="Controlled calibration agent MVP")
    parser.add_argument("--storage", type=Path, default=Path("runs"), help="Persistent run storage")
    parser.add_argument("-n", "--message", help="Send one natural-language instruction and wait for launched work")
    parser.add_argument("-r", "--session", help="Resume a conversation by session ID")
    commands = parser.add_subparsers(dest="command")
    chat = commands.add_parser("chat", help="Talk to the agent in natural language (the default)")
    chat.add_argument("--runtime", type=Path)
    chat.add_argument("--profile", type=Path)
    chat.add_argument("-n", "--message", default=argparse.SUPPRESS)
    chat.add_argument("-r", "--session", default=argparse.SUPPRESS)
    quantumx = commands.add_parser("quantumx", help="Run/query QuantumX function integration without an LLM")
    quantumx.add_argument("action", choices=["describe", "run", "result", "list", "compare", "stop", "demo"])
    quantumx.add_argument("identifier", nargs="?", help="Experiment name for run, experiment ID for result/stop/compare")
    quantumx.add_argument("second", nargs="?", help="Second experiment ID for compare")
    quantumx.add_argument("--params", default="{}", help="Experiment parameter JSON")
    quantumx.add_argument("--runtime", type=Path, default=Path("configs/runtime.quantumx.json"))
    for command in ["start", "resume"]:
        child = commands.add_parser(command)
        child.add_argument("--runtime", type=Path, help="Trusted runtime configuration JSON")
        child.add_argument("--max-steps", type=int, help="Pause after N transitions; does not finish the run")
        child.add_argument("--quiet", action="store_true", help="Print only the final snapshot")
        if command == "start":
            child.add_argument("--task", type=Path, required=True)
        else:
            child.add_argument("run_id")
    commands.add_parser("list")
    for command in ["status", "stop", "events"]:
        child = commands.add_parser(command)
        child.add_argument("run_id")
        if command == "events":
            child.add_argument("--after", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        if args.command in {None, "chat"}:
            from .terminal import launch_chat

            return launch_chat(args.storage, getattr(args, "runtime", None), getattr(args, "profile", None),
                               args.session, args.message)
        if args.command in {"start", "resume"} and args.max_steps is not None and args.max_steps < 1:
            raise ValueError("max-steps must be positive")
        store = Store(args.storage)
        if args.command == "quantumx":
            from .integrations.quantumx.cli import execute

            return execute(args, store, emit)
        elif args.command == "list":
            emit(store.list_runs())
        elif args.command == "status":
            state = store.load(args.run_id)
            emit({**state.model_dump(), "stop_requested": store.stopped(args.run_id)})
        elif args.command == "stop":
            store.request_stop(args.run_id)
            emit({"run_id": args.run_id, "stop_requested": True,
                  "message": "No further submissions after the stop boundary; in-flight jobs are not cancelled"})
        elif args.command == "events":
            store.load(args.run_id)
            for event in store.events(args.run_id, args.after):
                emit(event)
        else:
            from .bootstrap import create_controller, load_runtime, provenance
            from .graph import drive

            settings = load_runtime(args.runtime)
            controller = create_controller(store, settings)
            if args.command == "start":
                task = CalibrationTask.model_validate(read_json_config(args.task))
                run_id = controller.create(task, provenance(settings)).run_id
                if not args.quiet:
                    emit({"event": "run_started", "run_id": run_id})
            else:
                run_id = args.run_id
            report = drive(controller, run_id, args.max_steps, settings.poll_interval,
                           on_event=None if args.quiet else emit)
            emit({"event": "run_snapshot", **report, "report_path": str(store.root / run_id / "report.json")})
        return 0
    except KeyboardInterrupt:
        print("Interrupted. State is saved; inspect status and use resume with the same runtime configuration.", file=sys.stderr)
        return 130
    except Exception as exc:
        # Never print SDK request bodies or credentials on provider errors.
        from .storage import RunBusy

        if isinstance(exc, (ValueError, KeyError, FileNotFoundError, RunBusy)):
            print(f"Error: {exc}", file=sys.stderr)
        else:
            print(f"Execution interrupted by {type(exc).__name__}; inspect saved status before resuming.", file=sys.stderr)
        return 2
