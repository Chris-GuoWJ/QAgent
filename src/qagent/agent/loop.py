"""Persistent model/tool loop for QuantumX hardware experiments."""
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from qagent.agent.session import create_tools
from qagent.tools.experiments import START_TOOLS

from qagent.agent.prompts import SYSTEM


class ChatError(RuntimeError):
    pass


def visible_text(message):
    if isinstance(message.content, str):
        return message.content
    return "".join(b.get("text", "") for b in message.content
                   if isinstance(b, dict) and b.get("type") == "text")


class ChatAgent:
    def __init__(self, model, operations, max_tool_rounds=8):
        self.operations, self.history = operations, operations.history
        self.max_tool_rounds = max_tool_rounds
        self._call_id = None
        self.tools = {t.name: t for t in create_tools(operations, lambda: self._call_id)}
        self.model = model.bind_tools(list(self.tools.values()))

    def _execute(self, call, recovering=False):
        name, arguments, call_id = call.get("name"), call.get("args"), call.get("id")
        if not isinstance(call_id, str) or not call_id:
            raise ChatError("工具调用缺少编号，未执行操作")
        self._call_id = call_id
        try:
            if name not in self.tools:
                result = {"error": "此工具不可用，只能调用注册的 QuantumX 实验工具"}
            elif recovering and name in START_TOOLS:
                result = self.operations.quantumx.recover_operation(self.history.session_id, call_id)
            else:
                result = self.tools[name].invoke(arguments)
        except ValidationError:
            result = {"error": "工具参数不符合实验约定，请修正参数"}
        except (ValueError, KeyError) as exc:
            result = {"error": str(exc)}
        except Exception as exc:
            result = {"error": "操作未完成，请先查询实际实验状态", "error_type": type(exc).__name__}
        finally:
            self._call_id = None
        self.history.append(ToolMessage(content=json.dumps(result, ensure_ascii=False, allow_nan=False),
                                        tool_call_id=call_id, name=name))

    def _repair_pending_calls(self):
        messages = self.history.messages()
        last = next((i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], AIMessage)), None)
        if last is None or not messages[last].tool_calls:
            return
        completed = {m.tool_call_id for m in messages[last + 1:] if isinstance(m, ToolMessage)}
        for call in messages[last].tool_calls:
            if call["id"] not in completed:
                self._execute(call, recovering=True)

    def _context(self):
        messages = self.history.messages()
        boundaries = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
        if len(boundaries) > 12:
            messages = messages[boundaries[-12]:]
        facts = json.dumps(self.operations.task_info(), ensure_ascii=False)
        return [SystemMessage(content=SYSTEM + "\n当前能力和约定：\n" + facts), *messages]

    def respond(self, text):
        if not text.strip():
            return ""
        with self.operations.store.run_lock("chat-session-" + self.history.session_id):
            self._repair_pending_calls()
            self.operations.begin_turn()
            self.history.append(HumanMessage(content=text))
            for _ in range(self.max_tool_rounds):
                try:
                    response = self.model.invoke(self._context())
                except Exception as exc:
                    raise ChatError("模型调用失败（" + type(exc).__name__ + "），已有实验状态以记录为准") from None
                if self.operations.closing:
                    raise ChatError("会话正在退出，未执行后续工具调用")
                if not isinstance(response, AIMessage) or response.invalid_tool_calls:
                    raise ChatError("模型未返回有效消息或工具参数")
                if any(not c.get("id") for c in response.tool_calls):
                    raise ChatError("模型工具调用缺少编号")
                self.history.append(response)
                if not response.tool_calls:
                    return visible_text(response)
                for call in response.tool_calls:
                    self._execute(call)
            message = "本轮工具调用已达上限，已有实验记录保留，请继续查询状态。"
            self.history.append(AIMessage(content=message))
            return message
