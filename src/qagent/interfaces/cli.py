"""Hardware experiment CLI and natural-language conversation entry."""
import argparse
import json
import sys
from pathlib import Path

from qagent.app import DEFAULT_RUNTIME, create_service, create_store, load_runtime
from qagent.core.config import read_json_config


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="qagent", description="QuantumX hardware calibration agent")
    parser.add_argument("--storage", type=Path, help="Override state_dir from config/runtime.json")
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME, help="Trusted input configuration")
    parser.add_argument("-n", "--message", help="One natural-language instruction")
    parser.add_argument("-r", "--session", help="Conversation or CLI budget session ID")
    commands = parser.add_subparsers(dest="command")
    for name in ("chat", "describe", "check", "list", "run", "result", "stop", "compare"):
        child = commands.add_parser(name)
        child.add_argument("--runtime", type=Path, default=argparse.SUPPRESS)
        if name == "chat":
            child.add_argument("-n", "--message", default=argparse.SUPPRESS)
            child.add_argument("-r", "--session", default=argparse.SUPPRESS)
        if name == "run":
            child.add_argument("experiment", help="QuantumX calibration function name or CLI experiment ID")
            child.add_argument("--params-file", type=Path, help="JSON input parameters")
            child.add_argument("--params", default="{}", help="Explicit parameter JSON overrides")
        if name in {"result", "stop", "compare"}:
            child.add_argument("experiment_id")
        if name == "compare":
            child.add_argument("second_id")
    args = parser.parse_args(argv)
    service = None
    try:
        if args.command in {None, "chat"}:
            from qagent.interfaces.terminal import launch_chat

            return launch_chat(args.storage, args.runtime, args.session, args.message)
        settings = load_runtime(args.runtime)
        on_event = None
        if settings.experiment_cli is not None and args.command == "run":
            from qagent.interfaces.terminal import format_event

            def on_event(event):
                message = format_event(event)
                if message:
                    print(message, file=sys.stderr, flush=True)

        service = create_service(create_store(args.storage, settings), settings, on_event=on_event)
        if args.command == "check":
            external = settings.experiment_cli
            emit({"configuration_valid": True, "device_connected": False,
                  "backend": "experiment_cli" if external else "ZwBackend",
                  "result_directory": str(external.output_root if external else service.store.result_root),
                  "note": "配置已解析，未连接设备；驱动和采集需在实验电脑执行验证"})
        elif args.command == "describe":
            emit(service.describe())
        elif args.command == "list":
            emit(service.list_results())
        elif args.command == "result":
            emit(service.public_result(args.experiment_id))
        elif args.command == "stop":
            emit(service.stop(args.experiment_id))
        elif args.command == "compare":
            emit(service.compare(args.experiment_id, args.second_id))
        else:
            parameters = read_json_config(args.params_file) if args.params_file else {}
            parameters = parameters | json.loads(args.params)
            result = service.run_sync(args.experiment, parameters, args.session)
            emit(result)
            return 0 if result["status"] == "completed" and not result.get("export_error_type") else 2
        return 0
    except KeyboardInterrupt:
        print("已请求在下一次采集前停止，已有 IQ 和状态保留。", file=sys.stderr)
        return 130
    except Exception as exc:
        if isinstance(exc, (ValueError, FileNotFoundError)):
            print("配置或输入错误：" + str(exc), file=sys.stderr)
        else:
            print("操作失败（" + type(exc).__name__ + "），请查询已保存记录。", file=sys.stderr)
        return 2
    finally:
        if service is not None:
            service.pause()
            service.wait(timeout=2)
