# QAgent 使用与项目协作规范

## 系统用途

QAgent 是面向 QuantumX 真机校准的终端助手。使用者可以用中文了解实验、指定目标比特和扫描参数、启动采集、跟踪进度、比较结果，并将合格候选用于下一轮。QuantumX 负责生成脉冲、操作设备和保存测量数据。启用独立 CLI 后，QAgent 与实验程序分别使用各自的 Python 环境。

## 安装与配置

1. 在 Windows 上进入 QAgent 项目根目录，使用 Python 3.11 创建环境并安装项目。

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e ".[deepseek,test]"
   ```

2. 为 QAgent 准备运行配置。默认路径为 `config/runtime.json`，也可以用 `--runtime` 指定文件。配置在 `experiment_cli` 和 `quantumx` 两种模式中选择一种。

   - **独立 CLI 模式。** 建议在 `config/runtime-cli.json` 中配置 `experiment_cli`。填写 `python_executable`（实验环境的 Python 3.10.8 可执行文件）、`entrypoint`（QuantumX 的 `scripts/experiment_cli.py`）、`working_directory`（存放实验端 `experiment_cli.json` 的目录）和 `output_root`（两端共享的结果目录）。QuantumX 侧另行配置硬件、比特、驱动和设备锁，并使用相同的结果根目录。
   - **原有直连模式。** 在 `quantumx` 中填写 `source_dir`、`hardware_config`、`qubits_config`，并按需填写 `driver_dir` 和 `parameter_files`。QAgent 与实验库、驱动在同一个 Python 环境中运行，工具范围为读出中心、π 脉冲幅度和 Ramsey 精调。

3. 按实验权限设置 `allowed_qubits`、`max_experiments` 和 `max_measurements`。运行配置中的相对路径以配置文件所在目录为基准。对话需要本地配置 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`，按需配置 `DEEPSEEK_BASE_URL`。直接运行和查询命令不调用模型。

部署配置示例见 [README](README.md)，两端接口见 [实验系统对接说明](EXPERIMENT_SYSTEM_HANDOFF.md)。

## 日常使用

以下命令以独立 CLI 配置为例。在项目根目录执行，将尖括号中的编号替换为实际值。

```powershell
$qagentExe = ".\.venv\Scripts\qagent.exe"
$qagentRuntime = "config/runtime-cli.json"

& $qagentExe --runtime $qagentRuntime check
& $qagentExe --runtime $qagentRuntime describe
& $qagentExe --runtime $qagentRuntime chat
& $qagentExe --runtime $qagentRuntime -n "测量 q1 的 T1，扫描 31 个点"
& $qagentExe --runtime $qagentRuntime -r "<对话编号>"

& $qagentExe --runtime $qagentRuntime run calT1
& $qagentExe --runtime $qagentRuntime list
& $qagentExe --runtime $qagentRuntime result "<实验编号>"
& $qagentExe --runtime $qagentRuntime compare "<实验编号 A>" "<实验编号 B>"
& $qagentExe --runtime $qagentRuntime stop "<实验编号>"
```

| 使用者可以做什么 | 入口与行为 |
| --- | --- |
| 检查配置、查看当前实验范围 | `check` 检查运行配置。`describe` 读取实验能力、参数和默认值。两者均不发起采集。 |
| 学习实验并规划参数 | 在对话中询问“`calT2Echo` 测什么，延时用什么单位？”。Agent 可从校准 skill 获取原理、参数、前置条件和结果说明。 |
| 启动实验 | 在对话中明确指定实验、目标及所需参数，或用 `run <函数名或实验标识>` 直接执行。直接运行还支持 `--params-file`，与 `--params` 一起使用时以显式参数为准。 |
| 观察、查询和停止 | 终端会更新扫描点和 IQ 组数。用 `list`、`result` 或自然语言查询状态和文件位置。用 `stop` 请求协作停止，随后查询最终状态。 |
| 比较和继续校准 | `compare` 比较同一目标、同种实验的已完成结果。对话中可请求“用 q1 上一次合格读出中心候选，测量 π 幅度”。通过 `reference_experiment_id` 显式引用前轮结果。 |
| 恢复对话 | 用 `-r` 和原运行配置恢复对话。配置或运行身份变化时使用新会话。 |

CLI 模式以执行端 `describe` 为准。新版 QuantumX 提供 `calc_readout_centers`、`pituner`、`freqtuner`、`spectroscopy`、`calT1`、`calT2Echo`、`calXYZDelay`、`calPiHalf` 和 `calDRAG`。完整的 [单比特校准 skill](src/qagent/skills/single_qubit_calibration/SKILL.md) 收录了 36 个公开类方法。目录中的 `runnable` 表示当前连接的执行端允许 QAgent 调用该方法。

询问“从 VNA 扫完腔开始，单比特标定怎么做？”或“q5 卡在哪一步，下一步做什么？”时，Agent 查询[单比特校准流程 skill](src/qagent/skills/single-qubit-workflow/SKILL.md)和实际记录，区分物理依赖、当前可运行的实验与需要现场完成的环节。流程查询不会启动采集。

## 架构与运行机制

```text
终端对话或直接命令
  → QAgent 工具、校准知识、参数和会话预算
  → CLI 适配器写 request.json，调用 describe / validate / run / stop
  → 独立 QuantumX 进程调用 SingleQubitCalibration 和设备驱动
  ← JSONL 进度、持久化状态、原始 IQ、图像和结果清单
```

| 代码位置 | 职责 |
| --- | --- |
| `src/qagent/app.py`、`interfaces/` | 读取运行配置，装配实验服务，提供对话和命令行入口。 |
| `src/qagent/agent/`、`tools/` | 管理模型工具、对话历史和实验回执。CLI 模式按校准函数名注册工具，发送请求时映射为实验标识。 |
| `src/qagent/experiments/`、`skills/` | 维护已适配的实验、参数校验，以及可按函数名查询的校准知识。 |
| `src/qagent/integrations/experiment_cli/` | 通过配置指定的解释器启动独立进程，读取事件并核对状态、预算和产物。 |
| `src/qagent/core/` | 保存任务、会话、预算、去重与运行记录。原有直连执行路径在 `core/runner.py`。 |
| QuantumX 实验项目 | 负责实际扫描计划、波形校验、设备互斥、采集、数据保存和停止边界。 |

- **执行边界。** 解释、规划和查询均可单独进行。只有使用者明确要求执行时才提交任务，每条对话指令最多启动一个新实验。CLI 使用 QuantumX `validate` 返回的实际采集量核算预算。
- **进度与结果。** CLI 通过 stdout 发送逐行 JSON，实验端先持久化状态和产物。QAgent 读取任务目录中的 `status.json` 和 `result.json` 核对最终结论。CLI 实验数据位于 `output_root/<实验编号>/`，Agent 的日志和 SQLite 会话记录位于 `state_dir`。直连模式使用 `result_dir` 和 `input_archive_dir`。
- **停止与恢复。** 停止请求在执行端支持的边界生效。等待中的设备调用可能尚未返回，需继续查询。恢复对话时以已有任务和持久化状态为准，避免重新触发同一采集。
- **候选与迭代。** 区分运行完成、测量有效、候选合格和独立验收。只有合格的同目标结果可被下一轮显式引用，候选应用到本轮配置副本。当前由使用者逐轮发起，不支持无人值守的自动收敛。

协议细节和故障恢复见 [独立实验 CLI 设计](CLI_EXPERIMENT_DESIGN.md)。

## 配置文件访问

- 禁止读取、搜索、解析或展示 `.env`、`.env.*` 及其他 `*.env` 文件内容，目录内容检索必须排除它们。
- 上述限制针对开发 Agent 的内容访问，不禁止应用在用户启动时加载本地模型凭证。允许程序从当前目录 `.env` 加载必要的 DeepSeek 变量，其中密钥等敏感值不得输出、放入模型上下文或保存到运行报告；模型标识等非敏感元数据可按可追溯要求记录。
- 开发验证必须设置 `QAGENT_NO_ENV_FILE=1`，只使用替身加载器或无文件的合成测试数据，不能借运行程序或测试读取用户真实 `.env`。

## 版本管理

- 每一轮代码更新完成后，必须创建一次独立的 Git 提交，形成可回溯的版本。
- 提交信息应清楚说明本轮更改的内容和目的。
- 不得通过覆盖或合并历史提交来替代本轮的独立提交，以便追踪、比较和回退各轮更改。

## 测试与交付

- 每一轮更改都必须由 Agent 执行与更改内容相匹配的测试和验证，验证通过后才能提交并交付给用户。
- 若测试或验证失败，应先修复问题并重新验证，通过后再交付。
- 交付时应说明本轮更改、测试验证结果及对应的 Git 提交标识。
- 如因环境或依赖限制无法完成必要验证，必须明确说明阻塞原因和未验证的内容，不得将其作为已验证完成的成果交付。
