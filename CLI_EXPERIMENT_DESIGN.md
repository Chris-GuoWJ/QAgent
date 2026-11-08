# QAgent 与真机实验系统解耦设计方案

文档日期：2026-11-08

状态：待实现的设计方案；本文不代表当前代码已支持这些能力。
适用范围：QAgent 终端 Agent 与同一台 Windows 电脑上的真机实验系统。

## 1. 设计目标与已确定方向

QAgent 负责配置实验计划、启动实验、获取并分析结果，以及根据结果提出或执行后续参数迭代。真机实验系统负责实验算法、设备连接、数据采集、进度统计和实验产物生成。

两套系统分别运行在各自的 Python 环境中，通过 CLI 和文件协议协作，不相互导入代码，不要求安装对方的软件包。

已讨论确定的方向：

- QAgent 继续使用 Python 3.11，真机实验执行端继续使用已经验证可用的 Python 3.10.8 环境。
- QAgent 通过指定解释器启动实验系统提供的 CLI 入口，不在 Agent 进程中直接导入实验库和驱动。
- 实验系统对外提供稳定接口，不依赖 QAgent；人或其他程序也可以独立调用该 CLI。
- 用户继续在终端输入自然语言，模型仍通过结构化实验工具提交任务。
- 实验执行端提供实时进度，至少包括程序启动、完成的扫描点数、采集的 IQ 组数，以及结束后的原始数据和图像位置。
- QAgent 获取结果后进行分析，为下一轮实验生成参数；实时显示进度不需要逐条调用大模型。

下文的命令名、字段名、状态机和目录名属于建议的第一版实现约定。实现时应统一落实为同一版本协议，避免两端各自定义。

## 2. 当前实现与改造原因

当前 QAgent 的调用链是：

```text
自然语言 → 模型工具调用 → QuantumXService
         → 同进程导入实验库 → 实验函数 → RecordingBackend → 真机驱动
```

已核实的代码位置：

| 文件 | 当前职责 | 与本次改造的关系 |
| --- | --- | --- |
| `pyproject.toml` | 声明 Python >=3.11 | Agent 保持现有版本要求 |
| `src/qagent/tools/experiments.py` | 用 LangChain `@tool` 声明实验工具 | 可保留模型侧工具语义 |
| `src/qagent/agent/loop.py` | `bind_tools()` 和工具执行循环 | 继续调用结构化工具 |
| `src/qagent/core/runner.py` | 实验调度、后台线程、状态及预算 | 分离为 Agent 任务管理和 CLI 调用 |
| `src/qagent/integrations/quantumx/library.py` | 直接导入实验库和后端 | 新 CLI 模式不再调用此加载链 |
| `src/qagent/integrations/quantumx/recording_backend.py` | 每次采集后保存 IQ 和采集清单 | 其采集包装思路可在实验端独立实现 |
| `src/qagent/interfaces/terminal.py` | 目前显示实验开始、结束事件 | 扩展为阶段、计数和产物位置显示 |

当前使用的是本地 LangChain 工具调用，没有建立 MCP 客户端与服务端。后台线程仍使用 QAgent 的解释器，不能切换到真机 Python 3.10.8。

本次改造改变的是工具内部的执行方式，不取消工具注册，也不让模型直接生成任意命令行。

## 3. 目标架构与职责边界

```text
用户自然语言
    ↓
QAgent / Python 3.11
    模型：理解目标、选择实验、分析结果、规划下一轮参数
    工具与任务管理：校验计划、写请求、记录任务、查询和停止
    CLI 适配器：启动进程、接收事件、读取结果
    终端界面：持续显示状态、进度和文件位置
    ↓ 请求 JSON                         ↑ JSONL 事件 / 结果清单
实验系统 CLI / Python 3.10.8
    参数校验、实验映射、设备互斥、状态与停止处理
    采集包装层：统计进度、保存数据、发送事件
    原实验函数 → 真机驱动 → 设备
    ↓
实验端维护的原始数据、图像、结果清单与事件日志
```

| 能力 | QAgent | 真机实验系统 |
| --- | --- | --- |
| 理解自然语言、生成实验计划 | 负责 | 无需了解模型 |
| 实验参数检查 | 提交前检查工具参数、目标和预算 | 最终检查参数、设备约束和执行计划 |
| 启动实验 | 调用固定 CLI 入口 | 选择并调用注册的实验函数 |
| 驱动、连接、采集、拟合 | 不直接调用 | 负责 |
| 实时进度 | 接收、持久化摘要、展示 | 根据实际执行记录产生 |
| 原始 IQ 和实验图像 | 读取或引用 | 保存并报告位置 |
| 结果分析、参数迭代 | 负责 | 提供可读取的实验结果和质量指标 |
| 设备执行互斥、资源释放 | 可做本地调度 | 必须独立保障，包括从其他终端启动的任务 |

“独立”指部署和代码依赖独立；两端仍需遵守共同的数据协议。第一版按同机共享文件路径设计，暂不实现远程传输、MCP、HTTP 服务和常驻执行服务。

## 4. 第一版执行方式与配置

采用“每个实验任务启动一个独立进程”。`run` 在任务结束后退出，但在运行期间持续输出进度。QAgent 在后台监控进程，终端可继续处理查询或停止操作。

若后续证实驱动必须跨任务长期保持连接，再评估常驻执行服务；不在本轮默认引入。

建议在 Agent 运行配置中增加以下内容；示例路径并非当前电脑的真实部署路径：

```json
{
  "experiment_cli": {
    "protocol_version": "1",
    "python_executable": "D:/LabEnv/Scripts/python.exe",
    "entrypoint": "D:/LabExperiments/experiment_cli.py",
    "working_directory": "D:/LabExperiments",
    "output_root": "D:/ExperimentResults",
    "heartbeat_interval_seconds": 5
  }
}
```

配置要求：

- 解释器、入口和工作目录由本地配置确定，不由模型填写或修改。
- Agent 使用参数列表启动进程，设置 `shell=False`，不拼接可执行命令字符串。
- 显式使用目标 Python 的绝对路径，不依赖当前终端是否激活了某个环境。
- Python 入口使用 `-u`，输出统一为 UTF-8，事件输出及时刷新。
- 实验端所需环境变量和 DLL 搜索路径由部署配置提供，不把 Agent 的模型凭证传入实验请求或结果。
- 实验端只依赖其自己的实验库和运行环境，不导入 `qagent`，不安装 QAgent 的锁定依赖。
- 第一版 CLI 与 Agent 共享配置的结果根目录；跨机器运行需要另行增加文件传输及路径映射。

## 5. CLI 命令约定

以下命令是拟新增的实验系统入口，不是当前已有的 `qagent run` 命令。

```powershell
# 查询能力与参数约定，不连接设备
D:\LabEnv\Scripts\python.exe -u D:\LabExperiments\experiment_cli.py describe

# 校验请求并返回实际扫描计划，不连接设备
D:\LabEnv\Scripts\python.exe -u D:\LabExperiments\experiment_cli.py validate --request D:\ExperimentResults\exp-001\request.json

# 执行实验，在标准输出持续返回 JSONL 事件
D:\LabEnv\Scripts\python.exe -u D:\LabExperiments\experiment_cli.py run --request D:\ExperimentResults\exp-001\request.json

# 查询持久化任务状态，不发起新采集
D:\LabEnv\Scripts\python.exe -u D:\LabExperiments\experiment_cli.py status --experiment-dir D:\ExperimentResults\exp-001

# 提交停止请求；该命令返回不等于硬件已停止
D:\LabEnv\Scripts\python.exe -u D:\LabExperiments\experiment_cli.py stop --experiment-dir D:\ExperimentResults\exp-001
```

`describe` 返回协议版本、支持的实验标识、参数 schema、单位、默认值和能力说明。`validate` 返回生效参数、扫描轴、预计采集组数及 shots 预算；`run` 必须再次校验，不能依赖之前的校验结果。

第一版保留现有实验标识：`readout_centers`、`pi_amplitude`、`ramsey_frequency`。CLI 内部用明确映射选择函数，不接受任意脚本路径、模块名或可执行代码。

除 `run` 返回 JSONL 外，其余命令均返回一个 JSON 对象。`status`、`stop` 返回时包含任务编号和当前状态；`stop` 另返回 `stop_requested`。

## 6. 实验请求与迭代输入

每轮实验生成新的请求文件，保留实际输入快照。建议请求结构如下：

```json
{
  "protocol_version": "1",
  "experiment_id": "exp-001",
  "operation_key": "session-001-call-001",
  "experiment": "ramsey_frequency",
  "parameters": {
    "n": 1,
    "points": 50,
    "shots": 1024
  },
  "input_refs": {},
  "output_directory": "D:/ExperimentResults/exp-001",
  "parent_experiment_id": null,
  "iteration_index": 0
}
```

说明：示例只展示部分参数。完整字段、单位和必填项以 `describe` 返回的 schema 为准；执行端应将默认值展开后保存为 `effective_request.json`。

- `experiment_id`：本轮实验唯一标识；`operation_key`：同一工具操作的去重键。
- `parameters`：显式实验参数；不同实验使用各自 schema，不接受未声明的字段。
- `input_refs`：实验端约定的配置快照、前序候选或其他输入引用。具体引用格式随实验 schema 定义，不携带 Python 对象。
- `output_directory`：由 Agent 按本地结果根目录分配，实验端再次确认目录合法且任务身份一致。
- `parent_experiment_id` 和 `iteration_index`：记录迭代链，不表示可以自动无限执行。
- 每轮结果对应不可覆盖的输入快照和新输出目录；参数候选通过新请求显式应用，不隐式改写原始设备配置。
- 同一任务或同一操作重复提交时，返回已有任务信息或明确拒绝，不能再次采集。任务标识与参数不一致时拒绝执行。

## 7. 实时事件协议

### 7.1 输出通道

`run` 的标准输出只包含 JSONL：每行一个完整 JSON 对象，换行后立即刷新。普通日志、第三方库的打印和诊断信息写入标准错误或日志文件，不能混入事件流。

QAgent 同时持续读取标准输出和标准错误，避免任一输出管道堵塞。不能仅在进程退出后一次性读取全部输出。

实验端将相同事件追加到 `events.jsonl`，维护原子更新的 `status.json`。状态和日志先持久化，再向标准输出发送通知；Agent 接收不到输出时仍可查询文件确认事实。

事件公共字段：

| 字段 | 约定 |
| --- | --- |
| `protocol_version` | 字符串，第一版为 `1` |
| `experiment_id` | 本轮实验编号 |
| `seq` | 当前实验事件序号，从 1 开始严格递增；重连不重置 |
| `timestamp` | 带时区的 ISO 8601 时间，建议使用 UTC |
| `event` | 事件类型 |

### 7.2 最低事件集合

| 事件 | 含义 | 主要附加字段 |
| --- | --- | --- |
| `started` | 执行端已接受任务并进入运行流程，不等于已开始采集 | `phase`、生效请求路径、计划总数 |
| `progress` | 实际进度或阶段发生变化 | `phase`、扫描点数、采集组数、累计 shots |
| `heartbeat` | 执行程序仍有响应，计数可不变化 | 当前阶段、最近一次进展时间 |
| `completed` | 必需结果已持久化，实验执行成功 | 最终计数、结果清单路径、数据和图像目录 |
| `failed` | 执行失败 | 错误类别、说明、已保存的部分产物 |
| `cancelled` | 已响应停止请求并完成相应清理 | 停止原因、最终计数、部分产物位置 |

`phase` 建议使用 `validating`、`waiting_device`、`initializing`、`acquiring`、`fitting`、`exporting`。阶段变化可使用 `progress` 事件，计数保持原值。

以下为同一任务的简化事件示例，省略了中间的进度与心跳。时间仅用于说明格式：

```jsonl
{"protocol_version":"1","experiment_id":"exp-001","seq":1,"timestamp":"2026-11-08T02:00:00Z","event":"started","phase":"initializing","scan_points_done":0,"scan_points_total":50,"iq_groups_done":0,"iq_groups_total":100}
{"protocol_version":"1","experiment_id":"exp-001","seq":2,"timestamp":"2026-11-08T02:00:20Z","event":"progress","phase":"acquiring","scan_points_done":12,"scan_points_total":50,"iq_groups_done":24,"iq_groups_total":100,"shots_done":24576}
{"protocol_version":"1","experiment_id":"exp-001","seq":3,"timestamp":"2026-11-08T02:02:00Z","event":"completed","scan_points_done":50,"scan_points_total":50,"iq_groups_done":100,"iq_groups_total":100,"shots_done":102400,"result_file":"D:/ExperimentResults/exp-001/result.json","raw_data_dir":"D:/ExperimentResults/exp-001/raw","plot_dir":"D:/ExperimentResults/exp-001/plots"}
```

### 7.3 进度计数的明确含义

- `scan_points_done`：已完成全部必要采集且数据已保存的扫描点数。一个点有多条测量序列时，全部完成后才加 1。
- `iq_groups_done`：已完成且原始数据已保存的采集返回次数。一组通常包含多次 shots 的 IQ 样本，不能把一组当成一个 shot。
- `shots_done`：上述有效采集组中实际记录的 shots 总数。
- 例如 Ramsey 一个延迟点包括 X、Y 两条序列，可以是 50 个扫描点、100 组 IQ。具体关系以实验端计划为准，Agent 不猜测。
- 总数由执行端校验实际实验计划后给出。无法预先确定时用 `null`，终端只显示已完成数量。
- 采集失败、返回数据不合格或保存失败时，不增加“已完成”计数。
- 采集完成不等于拟合和导出完成，不把采集百分比直接称作整个实验的完成百分比或剩余时间估计。

### 7.4 进度的产生位置和能力边界

优先利用原实验库已有回调；其次在实验系统内增加采集包装层，在驱动返回并成功保存数据后更新计数。只有无法从外部观察采集边界时，才在原扫描循环增加进度回调。

当前 QAgent 的 `RecordingBackend` 提供了可参考的思路，但实验端实现不能依赖它所属的 `qagent` 包。应在实验系统中独立实现所需的薄适配层。

长时间阻塞的单次驱动调用，若驱动不提供内部进度，只能显示“正在采集、已等待多久”。进程存活和心跳表示程序仍存在或仍有响应，不证明设备正在推进。

心跳可由独立监控线程按配置周期发送，但必须同时携带最后实际进展时间。心跳超时显示“状态待确认”，不能自行认定实验成功、失败或自动重跑。

## 8. 结果清单、文件和数据归属

建议任务目录如下。QAgent 只写入请求；其余执行文件由实验端维护：

```text
D:/ExperimentResults/exp-001/
├── request.json             # QAgent 提交的请求
├── effective_request.json   # 实验端展开默认值后的实际输入和配置引用
├── status.json              # 原子更新的当前状态、阶段、计数和停止标记
├── events.jsonl             # 可重放的事件日志
├── experiment.log           # 人工诊断日志
├── result.json              # 最终结果清单，失败或停止时可记录部分结果
├── raw/                     # 原始 IQ 和关联扫描信息
└── plots/                   # 图像；没有图像时清单中的列表为空
```

`result.json` 至少包含：

| 内容 | 建议字段 |
| --- | --- |
| 身份与状态 | `protocol_version`、`experiment_id`、`operation_key`、`experiment`、`status` |
| 实际输入 | 生效请求路径、参数、扫描轴、配置快照引用 |
| 实验来源 | 实验库版本或源码标识、Python 版本、必要驱动版本；不包含凭证 |
| 执行记录 | 开始与结束时间、最终扫描点数、IQ 组数、shots 数 |
| 产物 | `artifacts` 列表，每项含 `role`、`path`、`format`、必要的形状/单位说明 |
| 计算结果 | `metrics`、拟合结果、参数候选及单位 |
| 质量判断 | `quality`、拟合是否有效、候选是否合格、是否经独立验证 |
| 异常 | `error`、`warnings` 和已保存的部分数据位置 |

约定：

- 本机第一版统一返回绝对路径，均位于已配置任务输出目录内；原始数据读取所需的 dtype、形状、列名、扫描轴和单位通过清单或关联元数据给出。
- 逐组数据保存、清单更新和终态写入采用明确的持久化顺序；完整 JSON 文件使用临时文件加原子替换，避免 Agent 读到半份内容。
- 必需数据和结果清单写好后才能发送 `completed`。图像生成失败可作为警告，但必须记录实际缺失项，不能返回不存在的图像文件。
- 实验端生成实验原生数据和图像；Agent 可另行生成自己的分析报告，并记录来源任务，不覆盖实验产物。
- 失败或停止也保留已经有效保存的数据，不能把部分数据作为完整实验结果使用。
- 进程退出码为 0 仅表示命令层正常结束；Agent 仍核对任务身份、最终状态、必需产物和结果清单。
- “执行完成”“拟合成功”“候选合格”和“校准经独立验证”分别记录，不能合并成一个成功标记。

## 9. Agent 的终端反馈和模型参与方式

目标用户体验：

```text
[实验 exp-001] 程序已启动，正在初始化
[实验 exp-001] 程序运行中：扫描点 12/50，IQ 24/100 组
[实验 exp-001] 采集完成，正在拟合
[实验 exp-001] 正在保存图像和结果
[实验 exp-001] 实验完成
  原始数据：D:\ExperimentResults\exp-001\raw
  图像：D:\ExperimentResults\exp-001\plots
  结果清单：D:\ExperimentResults\exp-001\result.json
```

事件接收、计数展示和状态落库由确定性程序完成，不把每条事件发给模型。高频事件可合并刷新终端，原始事件日志保留。

模型需要的信息是当前阶段和进度摘要，或者终态后的结构化结果、质量指标、必要的数据片段和图像。完整 IQ 默认保存在文件中，不直接全部写入模型上下文。

`start` 工具在任务登记并由执行端确认接受后返回任务回执；后台监控持续运行，不让一次长实验占满模型工具调用。若启动期间无法确认是否接受，返回“状态待确认”的回执，后续查询同一任务，不再次发起采集。

## 10. 停止、异常和恢复

### 10.1 状态与退出码

Agent 可记录 `submitted`、`starting`，执行端记录 `running`、`cancelling` 及终态 `completed`、`failed`、`cancelled`。`unknown` 是 Agent 对事实尚未确认的观察状态，不覆盖执行端已经确认的终态。

第一版建议 `run` 退出码：0 表示完成，1 表示执行失败，2 表示参数或协议错误，3 表示协作停止。其他命令的 0 表示命令成功处理，其任务状态以响应 JSON 为准。退出码是辅助信息，不能代替持久化状态。

### 10.2 协作停止

`stop` 在实验端原子写入独立的停止请求文件，并返回请求已记录；主执行进程在下一次可停止边界检查请求，停止后续采集并释放资源，最后记录 `cancelled`。

`status.json` 的最终状态由执行主进程维护，避免 `stop` 命令与它相互覆盖。正在阻塞的驱动调用是否能中断，取决于驱动能力，不能承诺立即结束。

常规停止不以强杀进程作为默认实现。Agent 退出时请求协作停止并等待确认；若超出等待时间，报告“停止待确认”，保留任务记录，不宣称已完成清理。

### 10.3 输出异常与恢复

- 非法 JSON、任务编号不匹配或不兼容协议版本：记录协议错误，并查询任务状态；不因解析失败再次启动实验。
- 事件使用任务编号和 `seq` 去重；发现序号缺口时从事件日志恢复，不能把重复事件重复计数。
- stdout 流结束但进程仍存活、进程异常退出、丢失终态事件：结合 `status.json`、事件日志和结果清单核对。
- 已持久化终态但未收到对应 stdout 事件：允许通过文件恢复终态。
- 收到 `completed` 但最终清单不存在、身份不符或必需文件缺失：标记结果不完整，停止参数迭代并保留诊断信息。
- 设备执行锁放在实验系统内，覆盖从 QAgent 和其他 CLI 启动的执行进程；Agent 侧互斥不能替代该锁。
- Agent 重启后查询已有任务，恢复展示和结果读取。是否存活不能只凭历史 PID 判断，也不能根据“没有收到结果”自动重跑。

## 11. 结果分析与参数迭代

目标闭环：

```text
目标和预算 → 生成计划 → 校验计划 → 执行实验 → 获取完整结果
                   ↑                         ↓
              下一轮新请求 ← 质量检查、分析、候选参数
```

每轮分析保留依据、输入实验编号、候选参数、单位和下一步理由。后续实验通过新请求显式引用前轮结果。

需区分两件事：CLI 解耦让 Agent 具备执行和结果访问接口；无人值守的多轮收敛仍需要另外实现迭代控制器。当前代码每条用户指令最多启动一个新实验，不能因完成 CLI 改造就默认取消此限制。

第一阶段保留现有单次启动行为。后续在用户给定的实验目标、参数范围、轮数/采集预算和停止条件内实现自动迭代；每次提交前重新检查约束。失败、停止、状态不确定、结果缺失或质量不足时，不直接生成下一轮采集。

## 12. 代码改造建议与实施顺序

建议 Agent 新增 `src/qagent/integrations/experiment_cli/`，包含配置、协议模型、进程客户端、事件读取和结果清单读取。实验端 CLI 放在实验系统自己的代码库中，目录由部署决定。

| 阶段 | 主要工作 | 完成标准 |
| --- | --- | --- |
| 1：固定协议 | 定义请求、事件、状态、结果 schema 和示例；明确实验标识、计数含义、退出码 | 两端可独立依据同一约定实现 |
| 2：实验端入口 | 实现 describe/validate/run/status/stop；先用替身采集，接入保存与事件输出 | Python 3.10.8 下独立运行，不依赖 QAgent |
| 3：Agent CLI 适配 | 指定解释器、写请求、异步监控、收集事件和结果 | Python 3.11 的 Agent 可调度独立进程 |
| 4：替换直接导入 | 重构 runner 和 app 装配，保留工具接口、预算、回执及候选引用 | CLI 模式不导入 QuantumX 或驱动 |
| 5：终端与恢复 | 进度展示、查询、停止、断线恢复、结果位置展示 | 实现第 9 节用户体验 |
| 6：真机验收 | 在目标电脑验证真实驱动、采集、保存、图像和停止边界 | 完成受控实验并核对原始数据 |
| 7：迭代控制 | 增加结果分析与有界多轮参数更新 | 每轮可追溯，满足预算和停止条件 |

现有模块的处理建议：

- `tools/experiments.py` 和 `agent/loop.py`：保留模型工具调用框架，调整服务接口和结果摘要。
- `core/runner.py`：保留任务、预算、去重和会话逻辑，移除 CLI 模式下直接创建后端和调用实验函数的路径。
- `experiments/registry.py`、`parameters.py`：保持 Agent 侧工具约束，与实验端 schema 做兼容性检查；实验端始终做最终校验。
- `app.py`：装配 CLI 服务，配置检查不得隐式导入实验库或连接设备。
- `interfaces/terminal.py`：处理新的阶段、进度与终态事件，持续输出文件位置。
- `reporting/`：区分实验端产物读取和 Agent 自有分析报告，避免两端重复写同一实验文件。
- `library.py`、`recording_backend.py`：迁移期间仅作为旧实现参考；是否保留旧模式应显式配置，CLI 失败时不得自动回退到直接真机调用。
- README 和使用文档：补充两个环境的部署方法、CLI 协议版本、结果目录和能力边界。

## 13. 验证与验收清单

实现阶段使用替身执行程序和替身采集验证接口，再进行目标电脑真机验收。离线测试不能证明驱动兼容性和真实采集正确性。

1. **环境隔离**：Agent 在 Python 3.11 运行，执行端报告 Python 3.10.8；实验端不安装 QAgent；Agent 的 CLI 路径不导入实验库与驱动。
2. **请求与校验**：合法请求生成可核对计划；非法参数、越界预算和不兼容协议在采集前被拒绝；重复提交不重复采集。
3. **实时性**：进程退出前终端已收到并显示进度；可在一个长任务中连续查询；stderr 大量输出不会堵住执行。
4. **计数正确性**：验证多序列扫描点、采集组数和 shots 的区别；失败组、未保存数据不计入完成数；阶段切换不重置计数。
5. **数据闭环**：完成后可从清单找到并读取原始数据和实际图像；核对形状、单位、扫描轴与最终计数；图像缺失被明确报告。
6. **异常与停止**：覆盖驱动报错、保存失败、协作停止、非法事件、缺失终态、事件重复、输出中断和异常退出；保留部分数据。
7. **恢复与互斥**：Agent 重启不自动重跑；终态可从文件恢复；从两个 CLI 进程发起的实验不能同时占用同一设备。
8. **迭代追溯**：后续阶段验证每轮新编号、新输入快照、前轮引用、候选单位、预算和停止条件；结果不完整时不继续采集。

开发验证遵守项目现有约定：设置 `QAGENT_NO_ENV_FILE=1`，不访问真实凭证文件，不在离线测试中连接设备。本文只是设计文档，本轮不启动实验、不更改运行配置和现有执行行为。

## 14. 参考说明

- [Python 子进程接口](https://docs.python.org/3.11/library/subprocess.html)：独立进程、输入输出管道和退出码。
- [Python 虚拟环境](https://docs.python.org/3.11/library/venv.html)：不同解释器及其依赖环境独立部署。
- [MCP 架构](https://modelcontextprotocol.io/docs/learn/architecture)：后续若需要标准工具服务，可另行采用 MCP；本方案第一版使用 CLI 协议。
- 当前架构参考：`docs/ARCHITECTURE.md`。本文描述目标实现，与当前行为不一致时应以“待实现”理解。
