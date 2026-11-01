# 实现与验证记录

日期：2026-11-01。当前版本：**0.6.0，QuantumX 真机专用入口与分层目录**。

## 0.6.0 目录整理与 Blueprint 分层参考

参照本地 Quantum-Calibration-Agent-Blueprint 的 core、tools、入口、data 和测试分组方式重构目录。当前源码分为 agent、core、experiments、tools、integrations、reporting、interfaces；app.py 负责装配，原 qagent.cli 保留为兼容入口，已有 qagent.exe 无需改命令。提示词单独放在 agent/prompts.py。测试划分 unit/integration，历史设计移入 docs/archive。

用户指定的 config/ 与 result/ 继续分别承载输入和人工结果。默认内部日志改为 data/state/journal.sqlite，锁放在 data/state/locks；pytest 缓存与临时数据改为 data/cache/、data/tmp/。config/runtime.json 新增 state_dir，--storage 仅覆盖内部状态位置。

实际整理已完成：旧 runs 完整归档到 data/archive/runs-before-v0.6，旧 build 与旧缓存保留到 data 下的归档/缓存子目录。整理前检查 1032 个旧锁文件，未发现活动锁。主 SQLite 通过备份接口迁移，迁移前后各表记录数一致（runs 4、events 45、chat_sessions 5、chat_messages 58），完整性检查为 ok。迁移记录位于 data/archive/layout-migration.json，旧数据库和文件均保留。

最终完整回归命令：python -m pytest -q --basetemp data/tmp/pytest-layout-final --tb=short。运行前设置 QAGENT_NO_ENV_FILE=1、LANGSMITH_TRACING=false、LANGCHAIN_TRACING_V2=false。结果：**56 passed，0 failed，0 skipped，26.11 秒**。新增测试覆盖默认状态路径、显式覆盖、锁目录互斥与存储重建读取；原 54 项测试随新模块路径运行通过。测试仅使用离线 IQ 替身和内存 HTTP 服务。

现有 .venv/Scripts/qagent.exe --help 通过；读取默认运行配置、创建存储并重新检查 SQLite 后，确认实际状态位置为 data/state，根目录没有重新生成 runs。代码内导入已全部更新，QuantumX2 与 Blueprint 原库未修改。本轮只整理组织和内部路径，没有扩大实验能力或自动调参范围，未进行真机设备验证。

当前架构与建议阅读顺序见 [ARCHITECTURE.md](ARCHITECTURE.md)。


## 0.5.0 历史交付

按照用户最新规划，移除旧 Q0 频谱示例、固定示例策略、模拟后端、旧控制器/LangGraph 和 demo 命令。仅保留 DeepSeek 对话及 QuantumX 三项实验服务：读出中心、π 脉冲幅度、Ramsey 频率精调。旧版验证记录保留在下文与 Git 历史中，不代表这些已移除模块仍对外提供。

默认 qagent.exe 读取 config/runtime.json。真机函数显式使用 RecordingBackend 包裹的 ZwBackend；后端根据本实验输入快照创建，接受可选 driver_dir。缺少真实配置会报错，不回退模拟模式。check 只解析配置，不连接设备。

config/experiments 保存可编辑的输入默认值，实际参数、扫描轴及硬件/比特配置归档到 config/history/quantumx/<编号>/。result/quantumx/<编号>/ 保存逐次原始 IQ NPY、CSV、PulseIR、采集标签、原函数结果、analysis.json、curve.csv、iq.png、fit.png、report.json 和 summary.html。只在实际有对应数据时生成文件，CSV 可完整往返还原 NPY 数据。散点图抽样显示；拟合使用原函数完整返回数据。

runs/ 继续保存业务日志和对话。真机版使用 quantumx_hardware_experiments / quantumx_hardware_sessions 表，旧模拟记录不被删除，也不阻塞真机版新任务。操作回执去重、预算预留、停止核对、退出中断和防止盲目重放继续生效。

模型可选择参数和引用合格候选，但没有连续多轮自动实验直到收敛的执行器。候选只用于新实验配置副本；原函数 update=False、show_plot=False。completed 和 accepted 不等于整项校准独立验证成功。

## 验证证据

完整执行：

~~~powershell
$env:QAGENT_NO_ENV_FILE = "1"
$env:LANGSMITH_TRACING = "false"
$env:LANGCHAIN_TRACING_V2 = "false"
.\.venv\Scripts\python.exe -m pytest -q --basetemp runs/pytest-hardware-release-fixed -p no:cacheprovider --tb=short
~~~

结果：**54 passed，0 failed，0 skipped，24.86 秒**。该测试集针对真机专用版本，替换了原示例控制器的测试；不能与旧版 175 项测试的覆盖范围直接比较。

覆盖三个 QuantumX 原函数完整候选链、真机工厂快照/驱动参数传递、默认参数和覆盖规则、输入与输出目录隔离、IQ NPY/CSV 精确往返、PNG 可解码、原库源码/配置/字节码缓存哈希不变、失败和中断数据保存、无采集失败状态页、预算去重、旧模拟日志隔离、退出与回执恢复、SDK 经内存 Mock HTTP 的工具协议及配置文件访问边界。

已人工查看 IQ 散点、Rabi 和 Ramsey 拟合 PNG，标签、图例和曲线显示正常。Windows 重复导出时遇到文件占用已修复：复用不可变的图像/CSV，导出错误单独记录，不改变已保存的采集或停止状态。该错误路径有专门测试。

CLI 帮助已核对。当前电脑执行默认 check 会明确提示缺少 D:/QAgent/QuantumX2/configs/hardware.json，符合本机尚无真实配置的状态；未用模板替代真机输入。迁移目标电脑需填写实际 hardware_config、qubits_config 和驱动环境。

所有测试仅使用 tests/support.py 中的 IQ 替身及内存 HTTP 服务，不读取真实凭证文件、不连接设备或在线模型。默认真机工厂通过替换类的边界测试验证，实际驱动加载、设备执行和在线模型决策尚未验收。未修改 QuantumX2 原文件。


## 历史版本记录

日期：2026-11-01

以下为 0.4.0 及以前的历史实现和验证材料，示例模块已在 0.5.0 移除。

## 1. 已实现范围

当前交付覆盖原设计阶段 B 的执行闭环，并提供自然语言 CLI 和阶段 C 所需的 DeepSeek 决策接入。用户在终端输入自然语言，由对话模型选择受控工具；校准过程的模型决策仍由现有控制器检查执行。尚未通过真实模型决策有效性评测或真机验收。

| 能力 | 当前实现 |
| --- | --- |
| 任务与动作契约 | Pydantic 校验目标、范围、预算及 scan / verify / finish 动作 |
| 控制流程 | LangGraph 调度日志驱动的决策、提交、等待、分析与核对节点 |
| 模拟隔离 | 独立适配器，启动装配层动态加载；核心模块只依赖公开协议 |
| 策略 | 固定频谱搜索基线；可配置的 DeepSeek 结构化决策接口 |
| 独立验证 | 新请求、新测量，核对质量、精度及与候选的频率差 |
| 记录 | SQLite 业务日志与 graph checkpoint、测量 JSON 和报告 |
| 恢复 | 复用后端任务或已有数据；提交不明确时先核对，无法核对时保持待核对 |
| 预算 | 提交前预留；接受后计费；确定未接受才释放；验证也计入预算 |
| 停止与互斥 | 持久化停止标志、提交起点排序，以及按运行编号持有的操作系统锁 |
| 对话 CLI | 无参数启动持续会话；自然语言启动、查询、报告、恢复和停止；支持单条指令和对话恢复 |
| 程序化 CLI | 保留 start、resume、status、events、stop、list |
| 后台进度 | 测量线程独立运行，终端显示真实事件，状态变化去重；对话退出在控制边界暂停 |
| 文件访问边界 | 无文件读写或 Shell 工具；显式配置路径拒绝 .env 及同类文件 |
| 本地凭证 | 用户启动程序时加载当前目录 .env 的 DeepSeek 变量，保留非空进程值优先级；开发验证禁用真实文件加载 |

## 2. 测试证据

在 Windows、Python 3.11.2 环境验证。依赖版本记录在 [requirements-lock.txt](../requirements-lock.txt)，主要版本为：LangGraph 1.2.19、SQLite checkpointer 3.1.3、langchain-deepseek 1.1.1、Pydantic 2.13.5、NumPy 2.4.6、SciPy 1.16.2、prompt-toolkit 3.0.53、pytest 9.1.1。

最终执行命令：

```powershell
$env:QAGENT_NO_ENV_FILE = "1"
.\.venv\Scripts\python.exe -m pytest -q --basetemp D:\QAgent\QAgent\runs\pytest-credential-loading -p no:cacheprovider
```

0.1.0 的执行框架回归曾通过 51 项测试；0.2.0 在同一控制器上增加对话与工具协议测试。全部记录均不包含在线 API 或真机实验。

0.2.0 最终结果：**88 passed，0 failed**，耗时 75.03 秒。包含真实 prompt-toolkit 交互循环的受控输入测试、真实 LangChain 客户端的本地 Mock HTTP 工具调用测试、Windows 环境文件大小写保护，以及已结束任务的停止反馈检查。

0.2.1 修正凭证加载边界：禁止开发 Agent 浏览凭证文件，不禁止用户应用在启动时加载凭证。新增测试采用替身加载器和隔离字典，覆盖变量白名单、进程值优先级、空值、自定义密钥变量、禁用开关、错误脱敏和模型配置接线；不读取任何实际 .env 内容。

0.2.1 验证结果：**96 passed，0 failed**，耗时 79.79 秒。开发验证过程始终禁用用户真实凭证文件加载；未核对用户实际变量值或进行在线 API 联调。

测试覆盖以下范围，具体测试定义见 `tests/`：

- 正常闭环、多个噪声条件、低质量数据增加测量、无目标时不误报成功。
- 越界参数、未知证据、错误模型输出、无验证的成功宣称。
- 实验与测量预算、停止前后预算及活动任务保留、时限和执行互斥。
- 提交结果不明确、无法核对、确认未接受后的重试，以及已接受未保存回执时的中断恢复。
- 已取数后的恢复、已有任务查询的暂时失败、实验失败和验证失败。
- 同一套后端契约测试作用于持久化模拟器与独立实现的测试后端。
- 核心模块在禁止导入模拟器与 DeepSeek 提供商模块的解释器中仍可加载。
- CLI 在独立进程中启动、查看、恢复、停止以及错误处理。
- DeepSeek 响应替身解析、模型可见信息边界、真实 LangChain 客户端离线构造与服务地址配置。
- 自然语言消息进入模型工具循环，对话与当前任务跨会话恢复，读操作不启动实验。
- 后台任务执行中的查询与停止、对话退出后的迟到模型响应不创建任务、旧策略任务恢复检查。
- 启动工具回执丢失时关联原任务，未执行的启动操作不会因对话恢复而被盲目重放。
- 真实 LangChain / DeepSeek SDK 经 Mock HTTP 完成工具调用和工具结果回传，全程无外部 API。
- 默认命令进入对话、单条自然语言指令、管道输入、交互终端以及打包的默认任务约定。
- 配置路径拒绝 .env 及同类文件，测试在读取函数执行前断言拒绝，不读取实际环境文件。

构建与命令行入口另外验证；SQLite 和原子文件写入使用工作区内的测试路径，以适配当前执行环境。测试中不发送真实 DeepSeek API 请求。

另用脚本化模型响应驱动实际 CLI 工具调用，完成三次实验及独立验证，确认终端显示自然语言进度。该样例验证输入、工具和执行器的连接，不证明真实模型理解自然语言的成功率。

## 3. 未验证或尚未实现

| 项目 | 当前限制 |
| --- | --- |
| DeepSeek 真实决策 | 尚未使用用户的 API 地址、模型和密钥发起在线请求；模型格式和策略能力仍需评测 |
| 独立分发包 | 已验证本地可编辑安装、命令入口和默认任务资源；最终 wheel 重建受构建依赖下载限制及本地构建工具缺失影响，本轮交付源代码 |
| 真机接入 | 没有真实设备驱动；外部工厂与端口已预留并通过替换测试 |
| 校准算法 | 0.3.0 支持单个 IQ 幅度 Lorentzian 峰/谷及旧概率正峰；多峰、非对称谱、多参数与漂移需要扩展分析方法 |
| 不确定度 | 使用拟合协方差和网格下限，不保证真机覆盖率或物理精度 |
| 预算结算 | 按完整已接受请求计费，不支持后端实际测量量的事后结算 |
| 任务取消 | 不自动取消外部任务，停止后保留活动回执或待核对状态 |
| 并发与部署 | 执行锁以单次运行编号为粒度；多用户调度和跨运行设备互斥尚未实现 |
| 待核对的人工处理 | 可以重新查询；尚无人工修改回执或释放未知请求预算的命令 |
| 界面与知识库 | 尚未引入 Web UI、VLM、多 Agent、知识检索或设备参数自动写回 |

固定策略执行闭环通过与 DeepSeek 客户端能够初始化，不构成真实模型 Agent MVP 的完整验收。后续应先完成实际 API 的协议检查，再在相同预算下比较正常与低质量数据的决策，以及相对固定策略的表现。

## 4. 2026-11-01 决策工具调用兼容性修复

用户报告对话正常，但校准在 `decide` 阶段连续报 `OpenAIInvalidRequestError`。本地持久化记录确认相关任务均为 0 次实验、0 次测量；原记录只保留异常类型，无法确认历史请求的完整服务端原因。

检查实际安装的 LangChain 实现，发现 `with_structured_output(..., method="function_calling")` 默认发送具名强制工具调用。DeepSeek 官方文档说明思考模式拒绝强制工具调用并返回 HTTP 400。使用真实 SDK 与本地 Mock HTTP 服务复现：HTTP 400 被 LangChain 包装为同名的 `OpenAIInvalidRequestError`。这验证了兼容性问题及其错误路径，但不能替代对用户实际服务的在线验证。

修复将内部决策请求改为 `tool_choice="auto"`，通过协议提示要求调用一次 `Decision`；Pydantic 解析、任务约定、预算检查及独立验证继续生效。保留 `json_mode` 选项和原有思考模式配置，不在失败时切换固定策略或重复提交实验。协议提示纳入策略身份，避免旧状态在不同协议下被静默恢复。

同时从异常原因链中提取数字 HTTP 状态码，附加应用生成的排查提示，并在终端显示已记录的失败原因。仍不保留服务端错误正文、请求地址、请求头、凭证或原始响应。

新增 `tests/test_decision_protocol.py`：真实 SDK 经内存 Mock HTTP 验证自动工具选择、三种思考模式配置、JSON 模式、不合法决策拒绝、HTTP 错误脱敏、包装异常状态提取和终端失败显示。闭环测试由固定基线在 Mock 服务中生成模型回复，实际运行模拟测量、数值分析及独立验证，不能证明真实模型的决策能力。

本轮开发和验证设置 `QAGENT_NO_ENV_FILE=1`，关闭远程追踪，未读取用户真实凭证文件，也未发送在线 API 请求。

验证结果：修复前新增协议回归测试复现 3 项失败；修复后定向测试 **24 passed**。完整回归执行 `python -m pytest -q --basetemp runs/pytest-decision-full-regression -p no:cacheprovider --tb=short`，结果为 **113 passed，0 failed**，耗时 86.02 秒。`git diff --check` 通过。现有虚拟环境为指向本仓库 `src/` 的可编辑安装，重新启动 CLI 即加载新代码。

参考：[DeepSeek Chat Completions 的 tool_choice 限制](https://api-docs.deepseek.com/api/create-chat-completion/)。

## 5. 0.3.0：默认 IQ 数据（2026-11-01）

按照用户要求，将公共 `Measurement` 默认数据类型从概率改为 IQ。V2 用独立的实数 I/Q 通道表示复数数据，支持 `[频率点]` 的已平均形式和 `[频率点][shot]` 的逐次采样形式。校验点数、shots、有限值、单位和通道维度；IQ 不再做概率域裁剪。

模拟后端默认产生已平均复数 IQ 谷谱，可显式选择峰谱和逐 shot 模式。IQ 使用 `backend-iq.sqlite`，显式概率兼容模式使用原 `backend.sqlite` 并保留 V1 场景指纹；不改写用户已有数据库或测量文件。持久化 V1 的 `y_unit="probability"` 仍按原类型读取。

数值分析按实际数据类型派生谱线：先平均 I/Q，再计算复数幅度与展开相位；IQ 峰/谷模型在内部缩放拟合坐标，原始单位和通道不被归一化覆盖。概率兼容模式保留原分析方法。控制器检查返回类型、峰/谷模式及验证数据的一致性，并为每轮测量保存 `.spectrum.json`；报告列出测量类型、峰/谷模式和谱线引用。

对话与决策提示明确 IQ 幅度不是激发态概率，模型仍只获取公开的分析摘要。QuantumX 真机接入和完整复数谐振器模型不在本轮实现范围内。

定向验证：`tests/test_iq.py`、`tests/test_controller.py`、`tests/test_contracts.py` 共 **71 passed**。验证内容含 IQ 正负/大幅值、逐 shot 复数平均、相位展开、跨数值尺度的峰/谷拟合、IQ 模拟闭环、概率兼容闭环、旧数据库身份和类型/模式错误拒绝。所有验证禁用真实凭证文件加载及远程追踪，只使用临时测试存储，不连接真实设备或在线模型。

完整回归结果：**145 passed，0 failed**，耗时 92.81 秒。执行命令为 `python -m pytest -q --basetemp runs/pytest-iq-full-regression -p no:cacheprovider --tb=short`，运行前设置 `QAGENT_NO_ENV_FILE=1` 并关闭远程追踪。默认运行配置、项目版本、设计文档源码链接和 Git 空白检查均通过。原有 `terminal.py` 未提交修改保持原状，不纳入本轮提交。

## 6. 0.4.0：QuantumX2 函数接入（2026-11-01）

新增可选的 `src/qagent/integrations/quantumx` 封装，直接调用当前 `D:\QAgent\QuantumX2` 工作树中的 `calc_readout_centers`、`pituner`、`freqtuner`。固定注册表和 Pydantic 参数模型限制函数与参数范围；原函数固定使用 `update=False`、`show_plot=False`。运行快照、候选传递和结果文件全部位于 QAgent 的存储目录，没有修改 QuantumX2 原内容。

RecordingBackend 透传 session/run，逐次原子保存 `(shots,2)` IQ 数组、PulseIR、扫描标签和采集清单。使用独立通用报告表达制备态、振幅和 Ramsey 延迟/X-Y 轴，不把它们套入旧频率扫描 Measurement。模型只获取指标、质量和文件位置。函数完成状态、合格候选和整项校准通过独立验证明确区分。

QuantumXService 增加持久化预算、调用回执去重、会话身份检查、后台执行、查询比较、停止与退出中断。失败保留部分 IQ 和整项预留预算；回执恢复不自动重放。新增自然语言工具和无需模型的 `quantumx` CLI，默认注入模拟 IQ，真实执行原函数的分类与拟合。未启用 quantumx 配置时仍使用原流程。具体用法见 [QUANTUMX2_USAGE.md](QUANTUMX2_USAGE.md)。

实际运行的三项演示均完成并产生合格候选：读出采集 2 次、Rabi 41 次、Ramsey 62 次，各点 512 shots，总计保存 53760 shots。Ramsey 在模拟真值偏移 100 kHz 时拟合修正约 101.31 kHz。文件示例位于 `runs/quantumx2-demo/quantumx/`；这些结果来自模拟信号，不能证明真实设备或在线模型的有效性。

新增 30 项测试覆盖真实函数的完整候选链、IQ 文件往返、扫描顺序、原库文件哈希与字节码缓存不变、错误数据拒绝、参数/目标/预算限制、重复调用、失败与中断部分保存、多个服务实例的停止核对、对话路由和回执恢复、单条指令限制，以及无需凭证的 CLI。缺少相邻 QuantumX2 时该可选测试组明确跳过。

完整回归命令：`python -m pytest -q --basetemp runs/pytest-quantumx-full-regression -p no:cacheprovider --tb=short`。结果：**175 passed，0 failed，0 skipped**，耗时 **97.76 秒**。运行时设置 `QAGENT_NO_ENV_FILE=1`、`LANGSMITH_TRACING=false`、`LANGCHAIN_TRACING_V2=false`；未读取真实凭证文件，不调用在线模型或真实设备。`terminal.py` 既有用户修改保持原状，不纳入本轮提交。
