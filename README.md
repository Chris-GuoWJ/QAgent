# QAgent：超导量子校准 Agent MVP

首版实现单比特频谱搜索的受控闭环：决策、参数校验、实验提交与等待、数值分析、独立验证和报告。支持固定基线策略与 DeepSeek API 决策；默认使用独立的模拟后端，不连接真实设备。

原需求见 [MVP_DESIGN.md](MVP_DESIGN.md)，技术设计见 [LANGCHAIN_IMPLEMENTATION_DESIGN.md](LANGCHAIN_IMPLEMENTATION_DESIGN.md)。当前实现范围与验证记录见 [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)。

## 1. 安装和首次运行

需要 Python 3.11 或以上。以下为 Windows PowerShell 命令，在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[deepseek,test]"
.\.venv\Scripts\qagent.exe --storage runs start --task configs/task.spectroscopy.json --runtime configs/runtime.fixed.json
```

固定策略无需 API 密钥。控制台逐行输出 JSON 事件，最后输出 `run_snapshot`，其中包含 `run_id`、状态、预算和报告位置。事件来自执行记录，不是模型生成的进度描述。`--quiet` 仅输出最后的快照。

复现本轮测试依赖时，可先安装 `requirements-lock.txt`，再执行 `python -m pip install -e . --no-deps`。该锁定清单对应本轮 Windows / Python 3.11 环境；全新环境及其他平台仍需验证。

`configs/task.spectroscopy.json` 的范围、预算和精度仅用于演示，不代表设备允许值或真机验收要求。`configs/runtime.fixed.json` 的模拟配置由启动装配层传给模拟适配器，不进入模型上下文。

## 2. 查看、暂停、恢复和停止

```powershell
# 执行两次状态转换后退出，保留可恢复状态。
.\.venv\Scripts\qagent.exe --storage runs start --task configs/task.spectroscopy.json --max-steps 2 --quiet

# 将 RUN_ID 替换为上一步返回的 run_id。
.\.venv\Scripts\qagent.exe --storage runs status RUN_ID
.\.venv\Scripts\qagent.exe --storage runs events RUN_ID
.\.venv\Scripts\qagent.exe --storage runs resume RUN_ID --quiet
.\.venv\Scripts\qagent.exe --storage runs list

# 从另一个终端提出停止请求。
.\.venv\Scripts\qagent.exe --storage runs stop RUN_ID
```

恢复时使用原来的 `--storage` 与 `--runtime`。系统检查后端和策略配置身份，防止把旧任务交给不同后端。Ctrl+C 后可先查看状态，再恢复；中断不会自动触发另一轮实验。

`stop` 将请求持久化。活动执行器在下一次控制边界处理；已暂停的运行可通过 `resume` 应用停止请求。提交开始与停止请求在数据库中排序：已经越过提交边界的请求属于在途请求。停止不会隐式取消后端任务；报告保留活动任务或待核对请求。

遇到无法核对的外部请求时，状态保持 `reconcile`，保留预算预留，不宣称校准成功，也不盲目重提。`resume` 可重新核对；当前没有人工覆盖后端回执的命令。

## 3. 使用 DeepSeek API

先安装 `deepseek` 可选依赖，设置环境变量：

```powershell
$env:DEEPSEEK_API_KEY = "你的密钥"
$env:DEEPSEEK_MODEL = "实际服务提供的模型标识"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
.\.venv\Scripts\qagent.exe --storage runs-deepseek start --task configs/task.spectroscopy.json --runtime configs/runtime.deepseek.json
```

如果使用第三方兼容网关，替换 `DEEPSEEK_BASE_URL` 和模型标识，并先确认其工具调用支持情况。应用不会自动读取 `.env`；`.env.example` 只是配置说明，不应填入真实密钥后提交。

默认通过 LangChain `ChatDeepSeek.with_structured_output()` 的 `function_calling` 模式产生 `scan`、`verify`、`finish` 决策。可以在运行配置的 `deepseek` 对象中选择 `json_mode`，但不同网关和模型的能力仍需真实请求验证。可选的 `thinking` 值为 `enabled` 或 `disabled`，未配置时不发送该参数。

模型只接收任务约定、剩余预算、后端能力、分析摘要和错误反馈，没有文件系统或 Shell 工具，也不会接收到模拟真值、噪声种子及连接密钥。模型调用的 SDK 自动重试设为 0，控制器按任务错误上限处理下一次决策调用，避免重试层级叠加。

密钥通过环境变量读取；模型调用异常正文不应作为运行事实保存。代码版本、策略配置摘要、分析和验证方法版本记录在运行中。当前不启用 LangSmith 远程追踪。

## 4. 代码结构

| 模块 | 职责 |
| --- | --- |
| `src/qagent/schemas.py`、`state.py` | 通用契约、持久化状态和模型可见视图 |
| `src/qagent/backend/base.py` | 实验端口与提交异常语义 |
| `src/qagent/backend/simulator.py` | 独立模拟适配器，使用单独数据库保存任务 |
| `src/qagent/bootstrap.py` | 动态加载已配置的适配器并注入控制器 |
| `src/qagent/controller.py` | 校验、预算、恢复、独立验证和结束规则 |
| `src/qagent/graph.py` | LangGraph 节点调度与 SQLite checkpoint |
| `src/qagent/policy.py` | 固定策略和决策策略端口 |
| `src/qagent/policies/deepseek.py` | 可选 DeepSeek 决策实现 |
| `src/qagent/analysis.py` | 数值拟合及质量判断 |
| `src/qagent/storage.py` | SQLite 业务日志、操作系统锁与原子写入的数据文件 |
| `src/qagent/cli.py` | 启动、查看、恢复和停止 |
| `tests/` | 控制器、后端契约、替换、恢复、CLI 和模型适配测试 |

核心控制器依赖 `ExperimentBackend` 协议；模拟器不由核心模块导入。模拟生成代码没有额外的专用依赖，分析所需的 NumPy/SciPy 属于分析依赖。真机 SDK 应由相应适配器按需导入。

## 5. 接入其他实验后端

实现 `ExperimentBackend` 协议，再提供工厂函数 `create_backend(options, root)`。在可信运行配置中设置：

```json
{
  "backend_factory": "my_lab.adapter:create_backend",
  "backend_options": {},
  "policy": "fixed",
  "poll_interval": 1
}
```

工厂配置会导入 Python 代码，只能使用自己信任的适配器。核心流程无需新增模拟或真机分支。后端需实现：

| 方法 | 语义 |
| --- | --- |
| `identity` | 同一外部系统与配置的稳定身份，用于恢复检查 |
| `capabilities()` | 实验类型、Hz 范围、采样限制、去重和请求检索能力 |
| `submit(request)` | 返回不透明的任务编号；明确拒绝使用 `SubmissionRejected`，不明确结果使用 `SubmissionUnknown` |
| `lookup(request_id)` | `found`、可靠的 `absent` 或 `unknown`；最终一致性索引的暂时未命中应返回 `unknown` |
| `status(job_id)` | 排队、运行、完成、失败或无法确认 |
| `fetch(job_id)` | 返回符合 `Measurement` 的数据、单位及原请求关联 |

单位与原始格式转换在适配器内完成。此次 MVP 的公共数据契约仅覆盖单比特频谱的 Hz 轴和概率响应；其他实验类型需扩展独立的实验契约和分析器，不应把设备特有字段塞进通用控制流程。

当前控制器按完整请求成本预留预算，接受后记账；失败的已接受任务仍计入已使用预算。真实后端的实际测量消耗结算、更精细的运行成本及任务取消尚未实现，需要在真机适配时定义。

## 6. 记录、恢复与分析边界

| 位置 | 内容 |
| --- | --- |
| `runs/journal.sqlite` | 任务快照、停止标志、动作、请求、预算和可补读事件 |
| `runs/checkpoints.sqlite` | 以 run_id 作为 thread_id 的 LangGraph checkpoint |
| `runs/backend.sqlite` | 示例模拟后端自己的任务记录，不由控制器直接读取 |
| `runs/<run_id>/<request_id>.json` | 本轮测量数据 |
| `runs/<run_id>/report.json` | 结论、证据、预算、活动请求与局限 |

每次 LangGraph 调用执行一次业务状态转换，再由驱动器推进下一步。恢复从业务日志分派阶段；提交意图已经登记且没有回执时，先核对后端。checkpoint 便于检查图执行，业务日志和后端回执共同决定是否允许下一次外部操作。

拟合器使用正峰 Lorentzian 模型，返回拟合中心、宽度参数、不确定度和残差信噪比。宽度参数为半高半宽。不确定度取拟合协方差估计与网格分辨率下限的较大值，只用于本次示例，未经真机统计覆盖率验证。真实响应存在多峰、负峰、漂移或模型失配时，应替换或扩展分析方法。

只有新的独立测量满足质量、精度和候选一致性规则，控制器才写入 `success`。实验程序正常返回或模型声称成功都不能替代验证。

## 7. 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

测试覆盖模拟后端与另一套独立后端的契约、受控闭环、不同噪声条件、无目标、越界、预算、停止、提交不明确、接受后中断、已有数据恢复、验证失败、后端替换、依赖隔离、跨进程 CLI 恢复及 DeepSeek 输出解析。

DeepSeek 单元测试使用响应替身及真实客户端的离线构造，不发送 API 请求。接入真实模型的自适应决策评测与真机验收仍需单独完成；固定策略闭环通过不等于完整 Agent MVP 已验收。
