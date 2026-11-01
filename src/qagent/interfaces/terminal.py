"""Natural-language terminal for hardware experiments."""
import asyncio
import hashlib
import sys

from qagent.app import create_service, create_store, deepseek_settings, load_runtime
from qagent.agent.loop import ChatAgent, ChatError
from qagent.agent.history import ChatHistory
from qagent.agent.session import ChatOperations
from qagent.agent.model import create_chat_model


def format_event(event):
    if event["event"] == "experiment_started":
        return "实验开始：" + event["experiment_type"] + "，编号 " + event["experiment_id"]
    if event["event"] == "experiment_finished":
        text = "实验 " + event["experiment_id"] + " 状态：" + event["status"]
        if event.get("export_error_type"):
            text += "；图像/页面导出失败（" + event["export_error_type"] + "），原始数据仍已保存"
        return text
    return None


def prepare_chat(storage, runtime_path=None, session_id=None, on_event=None):
    settings = load_runtime(runtime_path)
    service = create_service(create_store(storage, settings), settings, on_event=on_event)
    provider = deepseek_settings(settings)
    binding = service.identity + provider.model_dump_json()
    history = ChatHistory(service.store, hashlib.sha256(binding.encode()).hexdigest(), session_id)
    return ChatAgent(create_chat_model(provider), ChatOperations(service, history))


async def _interactive(agent, write):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout

    session = PromptSession()
    with patch_stdout():
        while True:
            try:
                text = await session.prompt_async("你 > ")
            except (EOFError, KeyboardInterrupt):
                break
            if text.strip() in {"/exit", "/quit"}:
                break
            if not text.strip():
                continue
            try:
                write("Agent > " + await asyncio.to_thread(agent.respond, text))
            except asyncio.CancelledError:
                agent.operations.pause()
                raise
            except ChatError as exc:
                write("Agent > " + str(exc))


def serve_terminal(agent, message=None, write=print):
    operations = agent.operations
    facts = operations.task_info()
    write("QAgent · QuantumX 真机实验终端")
    write("对话编号：" + agent.history.session_id)
    write("允许比特：" + ", ".join("q" + str(n) for n in facts["allowed_qubits"]))
    write("结果目录：" + facts["result_directory"])
    write("直接描述实验；可查询结果或要求停止。/exit 退出，已有 IQ 保留。")
    try:
        if message:
            write("Agent > " + agent.respond(message))
            operations.wait()
        elif sys.stdin.isatty():
            asyncio.run(_interactive(agent, write))
        else:
            for line in sys.stdin:
                if line.strip() in {"/exit", "/quit"}:
                    break
                if line.strip():
                    try:
                        write("Agent > " + agent.respond(line.strip()))
                    except ChatError as exc:
                        write("Agent > " + str(exc))
            else:
                operations.wait()
    finally:
        operations.close()
    return 0


def launch_chat(storage, runtime_path=None, session_id=None, message=None):
    def progress(event):
        text = format_event(event)
        if text:
            print("\n[实验] " + text, flush=True)

    return serve_terminal(prepare_chat(storage, runtime_path, session_id, progress), message)
