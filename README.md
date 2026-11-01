# QAgent：超导量子校准 Agent MVP

默认入口是自然语言 CLI：用户在终端与 DeepSeek 对话，由模型调用受控工具启动校准、查询进度、查看结果、恢复和停止任务。实验执行在后台进行，终端持续显示真实进度。当前支持单比特频谱校准，默认使用独立模拟后端，不连接真实设备。

**0.3.0 起测量数据默认是 IQ。** 后端返回 I、Q 两个通道，支持已平均和逐 shot 数据；数值分析先平均复数 IQ，再计算幅度、相位并拟合共振峰或谷。新模拟任务默认生成已平均 IQ 谷谱，数据不再被限定在 0–1；模型接收分析摘要，不直接处理整组 IQ 数组。

原需求见 [MVP_DESIGN.md](MVP_DESIGN.md)，技术设计见 [LANGCHAIN_IMPLEMENTATION_DESIGN.md](LANGCHAIN_IMPLEMENTATION_DESIGN.md)。当前实现范围与验证记录见 [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)。

## 1. 安装和首次运行

需要 Python 3.11 或以上。以下为 Windows PowerShell 命令，在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[deepseek,test]"

# 已在本机当前目录 .env 配置 DeepSeek 后，直接启动。
.\.venv\Scripts\qagent.exe
```

启动后直接输入自然语言，例如：

```text
你 > 你现在可以帮我做什么？
你 > 帮我校准 Q0 的共振频率
你 > 现在进行到哪一步了，还剩多少预算？
你 > 停止当前任务
你 > 看看刚才的实验结果，解释一下有没有通过验证
```

这些指令由模型理解，不是固定关键词匹配。工具只开放校准业务能力；没有文件读写、Shell 或直接修改设备参数的能力。启动器从当前目录 `.env` 加载必要模型凭证，模型与对话工具无法访问文件内容或密钥。所有实验参数仍受任务约定和控制器检查约束。

默认展示 Q0、4.8–5.2 GHz 的示例任务约定，最多 8 次实验。可在此约定内用自然语言收窄范围、减少预算、指定初始采样或收紧精度；超出目标、范围或预算的要求不会执行。其他目标或实验需要另行配置对应任务约定与分析能力。

实验在本 CLI 进程的后台线程执行，问进度、查看结果与请求停止不必等待完整校准结束。输入 `/exit` 或 Ctrl+D 退出对话，执行器会在控制边界暂停并保留任务；不会宣称设备任务已被取消。后台线程不是关闭终端后继续运行的系统服务。

可选入口：

```powershell
# 一条自然语言指令；如果启动了任务，会等待本进程执行器返回。
.\.venv\Scripts\qagent.exe -n "帮我校准 Q0 的共振频率"

# 使用启动时显示的对话编号恢复会话，模型保留前文和选中的任务。
.\.venv\Scripts\qagent.exe -r SESSION_ID

# 自定义可信运行配置和预先约定的任务范围。
.\.venv\Scripts\qagent.exe chat --runtime configs/runtime.deepseek.json --profile configs/task.spectroscopy.json
```

对话编号 `SESSION_ID` 与实验编号 `RUN_ID` 分别标识对话和校准任务。恢复对话不会盲目重放中断的启动或恢复工具调用；应先问已有任务状态，再明确要求继续。对话以 JSON 消息保存到业务数据库，不保存密钥配置。

如果会话使用了自定义运行配置或任务约定，恢复时也要传入原来的 `--runtime` 和 `--profile`。

恢复保留原任务的预算和墙钟时限，退出对话的时间仍计入时限。恢复不会自动延长授权时长；已过期时，控制器会结束任务并说明原因。

如需在没有 API 密钥时验证执行框架，仍可使用固定策略入口：

```powershell
.\.venv\Scripts\qagent.exe --storage runs-fixed start --task configs/task.spectroscopy.json --runtime configs/runtime.fixed.json
```

固定策略入口输出 JSON 事件和 `run_snapshot`，`--quiet` 仅输出最后快照；它是程序化执行接口，不承担自然语言理解。

复现本轮测试依赖时，可先安装 `requirements-lock.txt`，再执行 `python -m pip install -e . --no-deps`。该锁定清单对应本轮 Windows / Python 3.11 环境；全新环境及其他平台仍需验证。

`configs/task.spectroscopy.json` 的范围、预算和精度仅用于演示，不代表设备允许值或真机验收要求。`configs/runtime.fixed.json` 的模拟配置由启动装配层传给模拟适配器，不进入模型上下文。

## 2. 程序化命令：查看、暂停、恢复和停止

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

先安装 `deepseek` 可选依赖。在启动目录的 `.env` 中配置 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`，可选配置 `DEEPSEEK_BASE_URL`，再运行 `qagent`。虚拟环境本身不会自动把文件变成环境变量；应用的启动器负责加载。

也可以直接设置进程环境变量；已有的非空进程值优先于 `.env`，显式 JSON 模型配置仍优先于环境值。下面是可选的 PowerShell 配置方式：

```powershell
$env:DEEPSEEK_API_KEY = "你的密钥"
$env:DEEPSEEK_MODEL = "实际服务提供的模型标识"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
.\.venv\Scripts\qagent.exe --storage runs-deepseek start --task configs/task.spectroscopy.json --runtime configs/runtime.deepseek.json
```

如果使用第三方兼容网关，替换 `DEEPSEEK_BASE_URL` 和模型标识，并先确认其工具调用支持情况。启动器只导入 DeepSeek 变量及运行配置明确指定的密钥变量，不导入其他 `.env` 设置；不搜索上级目录，不进行变量插值。UTF-8 BOM 文件受支持。`.env` 不能作为任务或运行 JSON 文件传入，也不会提供给模型读取。

开发和自动化验证应设置 `QAGENT_NO_ENV_FILE=1`，禁用真实凭证文件加载。测试仅使用替身加载器或内存数据，不读取用户实际 `.env`，也不会输出密钥。

正常使用 `.env` 启动时不要设置该禁用开关；若之前在同一终端运行了开发命令，可用 `Remove-Item Env:QAGENT_NO_ENV_FILE -ErrorAction SilentlyContinue` 取消。pytest 的保护 fixture 也会在测试进程内自动禁用真实文件加载。

对话层通过 LangChain `bind_tools()` 选择校准业务工具，工具结果再返回模型形成自然语言答复。校准内部默认通过 `ChatDeepSeek.with_structured_output()` 的 `function_calling` 模式产生 `scan`、`verify`、`finish` 决策。可以在运行配置的 `deepseek` 对象中选择内部决策的 `json_mode`，但自然语言 CLI 仍要求模型支持工具调用。不同网关和模型的能力需真实请求验证。可选的 `thinking` 值为 `enabled` 或 `disabled`，未配置时不发送该参数。

模型只接收任务约定、剩余预算、后端能力、分析摘要和错误反馈，没有文件系统或 Shell 工具，也不会接收到模拟真值、噪声种子及连接密钥。模型调用的 SDK 自动重试设为 0，控制器按任务错误上限处理下一次决策调用，避免重试层级叠加。

内部决策的 `function_calling` 使用 `tool_choice="auto"`，并通过提示词要求调用一次 `Decision`，随后仍执行结构和控制器校验。这样避免 LangChain 默认的具名强制工具调用与 DeepSeek 思考模式或兼容网关冲突；没有返回工具调用或参数不合法时仍算作决策错误，不执行实验。`thinking` 未配置时仍由服务默认行为决定，不自动修改用户配置。

如果聊天正常、提交校准后却在 `decide` 阶段报 `OpenAIInvalidRequestError`，先检查模型服务的决策请求兼容性。新版本会从异常及其原因链中保留数字 HTTP 状态码，并显示本地生成的排查提示；不会保存服务端错误正文、请求头、凭证或原始模型回复。已失败任务的旧日志无法补回当时没有保存的 HTTP 信息。

更新后退出旧的 CLI 进程，重新启动并提交新任务，以载入修正后的决策协议。策略身份随协议更新而变化；旧会话与任务继续执行时仍受原来的身份一致性检查约束。

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
| `src/qagent/chat.py`、`chat_tools.py` | 自然语言模型工具循环及受控的校准操作 |
| `src/qagent/chat_history.py`、`terminal.py` | 持久化会话、终端输入和后台进度显示 |
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

单位与原始格式转换在适配器内完成。公共数据契约默认覆盖单比特频谱的 Hz 轴和 IQ 响应，保留显式概率数据的兼容入口；其他实验类型需扩展独立的实验契约和分析器，不应把设备特有字段塞进通用控制流程。

### IQ 数据契约

`Measurement` 默认 `schema_version=2`、`data_type="iq"`、`y_unit="a.u."`、`response_mode="dip"`。后端在 `capabilities()` 声明数据类型和峰/谷模式，控制器核对实际返回值，禁止执行过程中静默切换。物理电压可显式使用单位 `V`，未标定的 IQ 不能冒称为电压。

| 数据形式 | `iq.mode` | `iq.i`、`iq.q` 的形状 |
| --- | --- | --- |
| 已平均 IQ（默认） | `averaged` | `[频率点]`，保留后端返回的均值，不补造逐 shot 数据 |
| 逐次采样 IQ | `single_shot` | `[频率点][shot]`，每行长度必须等于 `shots_per_point` |

I/Q 允许负值和大于 1 的有限值。分析采用 `abs(mean(I) + 1j*mean(Q))`；不能使用 `mean(abs(I + 1j*Q))` 代替，也不会归一化成激发态概率。相位以弧度保存并展开。输入形状、频率轴、NaN/Inf、单位和混合类型均经过校验。

默认模拟器可在 `backend_options` 配置 `response_mode` 为 `peak`/`dip`，以及 `iq_mode` 为 `averaged`/`single_shot`。切换模拟场景后使用独立 `--storage`，避免把旧任务恢复到不同参数或数据形式的后端。

旧 JSON 若明确包含 `y_unit="probability"`，会继续按 V1 概率契约读取；新概率后端必须显式声明 `data_type="probability"`、`response_mode="peak"`，测量中提供 `values`，不能同时提供 `iq`。旧模拟器可用 `backend_options` 中的 `"data_type": "probability", "response_mode": "peak"` 显式选择。未标注类型/概率单位的 `values` 不会被猜测成 IQ。

默认 IQ 模拟器使用新的 `backend-iq.sqlite`，旧的 `backend.sqlite` 和已有测量文件保持原样；显式概率模式保留原场景身份。数据形式和策略提示发生变化后应启动新会话，历史任务继续受后端及策略身份一致性检查约束。

当前控制器按完整请求成本预留预算，接受后记账；失败的已接受任务仍计入已使用预算。真实后端的实际测量消耗结算、更精细的运行成本及任务取消尚未实现，需要在真机适配时定义。

## 6. 记录、恢复与分析边界

| 位置 | 内容 |
| --- | --- |
| `runs/journal.sqlite` | 任务快照、停止标志、动作、请求、预算和可补读事件 |
| `runs/checkpoints.sqlite` | 以 run_id 作为 thread_id 的 LangGraph checkpoint |
| `runs/backend-iq.sqlite` | 默认 IQ 模拟后端自己的任务记录，不由控制器直接读取 |
| `runs/backend.sqlite` | 显式概率兼容模式的后端记录；已有数据库不自动转换 |
| `runs/<run_id>/<request_id>.json` | 本轮测量数据 |
| `runs/<run_id>/<request_id>.spectrum.json` | IQ 均值、幅度、展开相位及单位；已平均输入不包含伪造的单次采样 |
| `runs/<run_id>/report.json` | 结论、证据、预算、活动请求与局限 |

启用对话后，`journal.sqlite` 另外保存对话会话和模型消息，采用显式 JSON 消息序列化。进程重启后的对话恢复与实验恢复分别处理；已经创建的实验通过业务日志关联，不因丢失一条聊天工具回执而重新创建。

每次 LangGraph 调用执行一次业务状态转换，再由驱动器推进下一步。恢复从业务日志分派阶段；提交意图已经登记且没有回执时，先核对后端。checkpoint 便于检查图执行，业务日志和后端回执共同决定是否允许下一次外部操作。

拟合器使用正峰 Lorentzian 模型，返回拟合中心、宽度参数、不确定度和残差信噪比。宽度参数为半高半宽。不确定度取拟合协方差估计与网格分辨率下限的较大值，只用于本次示例，未经真机统计覆盖率验证。真实响应存在多峰、负峰、漂移或模型失配时，应替换或扩展分析方法。

只有新的独立测量满足质量、精度和候选一致性规则，控制器才写入 `success`。实验程序正常返回或模型声称成功都不能替代验证。

## 7. 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

测试覆盖模拟后端与另一套独立后端的契约、受控闭环、不同噪声条件、无目标、越界、预算、停止、提交不明确、接受后中断、已有数据恢复、验证失败、后端替换、依赖隔离、跨进程 CLI 恢复及 DeepSeek 输出解析。

DeepSeek 单元测试使用响应替身及真实客户端的离线构造，不发送 API 请求。接入真实模型的自适应决策评测与真机验收仍需单独完成；固定策略闭环通过不等于完整 Agent MVP 已验收。
