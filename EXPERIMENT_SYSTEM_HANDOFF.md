# QAgent 与真机实验系统对接说明

本文供真机实验系统的开发者使用。QAgent 侧已经实现 CLI v1 调用。实验端提供独立命令行入口、实时进度和结果文件后，两边即可在目标电脑联调。

## 1. 两套代码如何协作

```text
用户输入自然语言
    ↓
QAgent 项目 · Python 3.11
    选择实验 → 校验参数与预算 → 生成 request.json → 启动独立进程
    ↓ 调用指定的 Python 3.10.8 和 CLI 入口
真机实验项目 · Python 3.10.8
    CLI 入口 → 原实验函数 → 驱动 → 真机设备
    ↓ stdout 进度事件 + 任务目录中的状态、数据和图像
QAgent 展示进度 → 核对结果 → 分析并准备下一轮参数
```

| 系统 | 代码归属与职责 |
| --- | --- |
| QAgent | 模型工具、参数与会话预算、进程启动、终端显示、任务查询和结果分析都在 QAgent 项目中。CLI 调用代码位于 `src/qagent/integrations/experiment_cli/`。 |
| 真机实验系统 | CLI 入口、实验映射、实验函数、设备配置和驱动都由实验项目维护。执行端负责设备互斥、协作停止、数据保存、图像生成和真实状态。 |
| 两边的接口 | 同机部署，共享任务目录。双方通过 CLI 参数、JSON 请求、逐行 JSON 事件和结果文件交换数据。各自在自己的环境安装依赖。 |

实验端的 CLI 入口可以是一层薄包装，内部按实验标识调用原有函数。采集进度可以从已有回调或采集包装层获得。QAgent 通过进程启动调用入口文件，两套代码无须互相导入或安装。独立运行实验 CLI 时也可以直接提交同格式的任务。

## 2. 部署时需要提供什么

| QAgent 配置项 | 实验端需要提供的信息 |
| --- | --- |
| `python_executable` | 已安装真机实验依赖的 Python 3.10.8 可执行文件路径，例如 `D:/LabEnv/Scripts/python.exe` |
| `entrypoint` | 实现下述命令的 Python 入口文件路径，例如 `D:/LabExperiments/experiment_cli.py` |
| `working_directory` | 启动 CLI 时使用的工作目录，实验端可据此定位自己的配置 |
| `output_root` | 两个进程均可访问的结果根目录。QAgent 在其下按实验编号建立任务目录 |

QAgent 在运行配置中使用 `experiment_cli` 配置段。若实验系统已有另一套命令格式，可以在实验项目中用薄入口转换为 v1。联调时可提供现有命令的 `--help` 输出及一轮完整的进度、状态和结果样例。

## 3. 实验端需要实现的命令

下表列出入口文件接收的参数。QAgent 使用配置的解释器和工作目录启动程序。

| 命令 | 实验端返回什么 |
| --- | --- |
| `describe` | 返回一个 JSON 对象，包含 `protocol_version: "1"` 和 `experiments`。每项含实验 `name` 和可选的 `defaults`。建议用 `data_origin` 标明 `hardware` 或 `test`。 |
| `validate --request <request.json>` | 在连接设备前校验任务。返回一个 JSON 对象，包含协议版本、`experiment_id`、`scan_points_total`、`iq_groups_total` 和 `planned_measurements`。 |
| `run --request <request.json>` | 再次核对计划，独立执行实验。在标准输出持续发送进度事件，并在任务目录保存状态、数据、图像和结果清单。 |
| `stop --experiment-dir <任务目录>` | 持久化停止请求，返回包含协议版本、`experiment_id` 和 `stop_requested` 的 JSON 对象。执行进程在可停止的边界结束后记录 `cancelled`。 |
| `status --experiment-dir <任务目录>` | 可供人工查询当前状态。QAgent 的查询和恢复直接读取 `status.json` 与 `result.json`。 |

`describe` 和 `validate` 可在无设备连接时运行。QAgent 按 `describe` 中已适配的实验注册工具，模型侧工具名使用 `method` 对应的校准函数名，请求仍发送原实验标识 `name`。函数及其参数说明见 [单比特校准 skill](src/qagent/skills/single_qubit_calibration/SKILL.md)。

已知采集组数时，`planned_measurements` 等于 `iq_groups_total × parameters.shots`。若总数无法预知，允许将相应总数字段设为 `null`，同时提供正整数 shots 上限。运行时应遵守预检计划和设备约束。

## 4. QAgent 交给实验端的输入

QAgent 为每轮写入一个 `request.json`，通过 `--request` 传入文件路径。

| 请求字段 | 含义 |
| --- | --- |
| `protocol_version` | 字符串 `"1"` |
| `experiment_id`、`operation_key` | 任务身份和去重键。相同任务重复提交时保留已有执行记录，避免重复采集 |
| `experiment`、`parameters` | 实验标识及校验后的参数，如目标比特 `n`、每组 `shots`、点数和扫描范围 |
| `output_directory` | 当前任务的绝对目录。实验端在其中保存自己的状态和产物 |
| `input_refs`、`parent_experiment_id`、`iteration_index` | 前轮引用与迭代关系。引用合格候选时，`input_refs.candidate_result` 是前轮结果清单的绝对路径 |

实验端负责展开硬件相关默认值、核对实际输入并保存快照。收到前轮候选时，由实验端验证目标和单位，并应用到本轮配置副本。当前每轮由用户启动，QAgent 可提出并传递下一轮参数。

## 5. 实验端要怎样反馈进度

`run` 使用 UTF-8，每个 stdout 行是一个 JSON 对象，换行后立即刷新。普通日志走 stderr 或实验日志文件。每条事件携带 `protocol_version`、`experiment_id`、从 1 递增的 `seq`、带时区的 `timestamp` 和 `event`。

| 事件 | QAgent 可以展示的信息 |
| --- | --- |
| `started` | 程序已接受任务，进入初始化或等待设备阶段 |
| `progress` | 当前 `phase`、`scan_points_done` / `scan_points_total`、`iq_groups_done` / `iq_groups_total` 和 `shots_done` |
| `heartbeat` | 程序仍有响应。长时间采集时可保持计数不变 |
| `completed`、`failed`、`cancelled` | 完成、失败或已协作停止。最终结论以任务目录中的持久化记录为准 |

扫描点只有在该点所需采集全部完成并保存后才计入。IQ 组数按已成功保存的采集组计数，一组通常包含多次 shots。例如 Ramsey 的 X、Y 两条序列可以对应一个扫描点和两组 IQ。采集组数增加时同步更新 `shots_done`。支持的阶段为 `validating`、`waiting_device`、`initializing`、`acquiring`、`fitting` 和 `exporting`。

在发送通知前，将事件追加到 `events.jsonl`，并原子更新 `status.json`。状态文件包含协议版本、任务编号、序号、`status`、阶段和当前计数。执行中使用 `running` 或 `cancelling`，结束后使用 `completed`、`failed` 或 `cancelled`。这样即使终端重启，Agent 仍可查询同一任务。

## 6. 结束时需要交付的结果

```text
<output_directory>/
├── request.json             QAgent 提交的请求
├── effective_request.json   实验端记录的生效输入
├── events.jsonl             实验端事件日志
├── status.json              当前状态和计数
├── result.json              实验端结果清单
├── raw/                     原始 IQ 与关联数据
└── plots/                   已生成的图像
```

成功时，`result.json` 包含协议版本、`experiment_id`、`operation_key`、`experiment`、`status: "completed"`、最终的 `scan_points_done`、`iq_groups_done`、`shots_done` 和 `artifacts` 列表。身份与请求一致，计数与最终状态一致。

- **原始数据。** `artifacts` 至少有一个 `role: "raw_iq"` 条目。每个条目的 `path` 是任务目录内实际存在的绝对文件路径。建议附上格式、形状、单位和扫描轴等信息。
- **图像。** 有图像时，使用 `role: "plot"` 列出文件。图像缺失时，QAgent 会显示实际数据位置和缺失提示。
- **分析指标与候选。** 提供 `metrics` 与 `quality` 对象，包括拟合指标、候选值及单位。只有 `quality.accepted: true` 的同目标已完成结果才能作为下一轮的候选引用。
- **保存顺序。** 先保存数据、图像和结果清单，再写最终状态并发送终态事件。失败或停止时保留已写入的部分数据，并在可用时返回其清单。

QAgent 从结果清单提取位置和指标，用于终端展示、结果比较及下一轮规划。设备互斥和停止后的资源释放始终由实验端负责。

## 7. 联调时核对的行为

1. `describe` 能列出实验，`validate` 能返回正确预算，两个命令都可离线运行。
2. 执行中终端先收到启动，再收到扫描点和 IQ 组数更新。
3. 实验结束后，QAgent 能读到一致的终态，并定位清单所列的原始数据和图像。
4. 停止请求可被记录，执行端在确认停止后返回 `cancelled`，已保存的部分数据仍可查阅。
5. QAgent 重启后仍可查询同一任务，重复提交同一个任务不会再次采集。

完整协议示例见 [CLI 实验设计方案](CLI_EXPERIMENT_DESIGN.md)。可独立运行的命令与事件样例见 [离线替身程序](tests/fixtures/experiment_cli.py)，它用于接口验证。
