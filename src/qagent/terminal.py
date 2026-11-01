"""Conversational terminal; experiment execution stays in a background worker."""

import asyncio
import hashlib
import json
import sys
from importlib.resources import files

from .bootstrap import create_controller, deepseek_settings, load_runtime, provenance
from .chat import ChatAgent, ChatError
from .chat_history import ChatHistory
from .chat_tools import ChatOperations
from .config import read_json_config
from .policies.deepseek import create_chat_model
from .schemas import CalibrationTask
from .storage import Store


def format_event(event):
    kind = event["event"]
    if kind == "decision_accepted":
        config = event["config"]
        label = "独立验证" if event["purpose"] == "verification" else "频谱扫描"
        return f"{label}：{config['start_hz']/1e9:.6f}–{config['stop_hz']/1e9:.6f} GHz，{config['points']} 个点"
    if kind == "job_accepted":
        return "实验后端已接受任务，正在等待测量结果"
    if kind == "job_progress":
        return {"queued": "设备任务正在排队", "running": "设备任务正在运行"}.get(event.get("status"))
    if kind == "analysis_completed":
        analysis = event["analysis"]
        if analysis["trusted"]:
            return f"分析完成：候选频率 {analysis['frequency_hz']/1e9:.9f} GHz，估计不确定度 {analysis['uncertainty_hz']/1e3:.1f} kHz"
        return "数据质量尚不足以接受候选参数，正在调整策略"
    if kind == "run_finished":
        label = {"success": "校准通过独立验证", "not_converged": "任务结束，尚未收敛",
                 "failed": "任务因错误结束", "stopped": "任务已停止"}[event["outcome"]]
        analysis = event.get("analysis")
        if analysis:
            label += f"；验证频率 {analysis['frequency_hz']/1e9:.9f} GHz"
        if event["outcome"] == "failed" and event.get("reason"):
            label += "；" + event["reason"]
        return label
    if kind in {"submission_unknown", "job_unknown", "reconciliation_required"}:
        return "外部任务状态待核对；已阻止继续提交实验"
    if kind == "stop_requested":
        return "已收到停止请求"
    if kind == "executor_error":
        return "执行器已中断（" + event["error_type"] + "），已有状态可查询和恢复"
    if kind == "executor_busy":
        return "任务已有其他执行器，查询和停止请求仍使用持久化运行记录"
    if kind == "executor_returned" and not event["outcome"]:
        return "执行器已暂停，任务状态为 " + event["stage"]
    return None


def prepare_chat(storage, runtime_path=None, profile_path=None, session_id=None, on_event=None):
    # A plain `qagent` launches a real model conversation, never a fixed keyword parser.
    settings = load_runtime(runtime_path)
    if runtime_path is None:
        settings = settings.model_copy(update={"policy": "deepseek"})
    provider = deepseek_settings(settings)
    model = create_chat_model(provider)
    profile_data = (read_json_config(profile_path) if profile_path else
                    json.loads(files("qagent").joinpath("resources/chat-profile.json").read_text(encoding="utf-8")))
    profile = CalibrationTask.model_validate(profile_data)
    store = Store(storage)
    controller = create_controller(store, settings)
    public_binding = controller.backend.identity + controller.policy.identity + profile.model_dump_json() + provider.model_dump_json()
    context_id = hashlib.sha256(public_binding.encode()).hexdigest()
    history = ChatHistory(store, context_id, session_id)
    operations = ChatOperations(controller, profile, history, settings.poll_interval,
        metadata=provenance(settings), on_event=on_event)
    return ChatAgent(model, operations)


async def _interactive(agent, write):
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.patch_stdout import patch_stdout
    except ImportError:
        raise ValueError('请安装交互终端依赖：python -m pip install -e ".[deepseek]"') from None

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
                response = await asyncio.to_thread(agent.respond, text)
                write("Agent > " + response)
            except asyncio.CancelledError:
                operations = agent.operations
                operations.pause()
                raise
            except ChatError as exc:
                write("Agent > " + str(exc))


def serve_terminal(agent, message=None, write=print):
    operations = agent.operations
    profile = operations.profile
    write("QAgent · 自然语言校准终端")
    write("对话编号：" + agent.history.session_id)
    write(f"任务约定：{profile.qubit}，{profile.lower_hz/1e9:g}–{profile.upper_hz/1e9:g} GHz，最多 {profile.max_experiments} 次实验、{profile.max_measurements} 次测量")
    write("直接描述任务即可；可随时问进度或要求停止。/exit 退出并保留恢复状态。")
    try:
        if message:
            write("Agent > " + agent.respond(message))
            operations.wait()
        elif sys.stdin.isatty():
            asyncio.run(_interactive(agent, write))
        else:
            # Enables pipes and reproducible CLI tests without terminal emulation.
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


def launch_chat(storage, runtime_path=None, profile_path=None, session_id=None, message=None):
    last_status = {}

    def progress(event):
        if event["event"] == "job_progress":
            key = (event["run_id"], event["job_id"])
            if last_status.get(key) == event["status"]:
                return
            last_status[key] = event["status"]
        text = format_event(event)
        if text:
            print("\n[实验] " + text, flush=True)

    agent = prepare_chat(storage, runtime_path, profile_path, session_id, progress)
    return serve_terminal(agent, message)
