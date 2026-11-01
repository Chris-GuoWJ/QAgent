# QAgent：QuantumX 真机校准 Agent

当前版本 0.5.0。默认入口使用 DeepSeek 自然语言对话，调用 QuantumX2 原实验函数，通过 ZwBackend 获取真机 IQ。当前支持读出中心、π 脉冲幅度和 Ramsey 频率精调。

旧 Q0 频谱示例、固定示例策略、内置模拟后端和 demo 命令已移除。QuantumX2 原文件不作修改。测试使用的 IQ 替身只在 tests 中存在。

## 安装与输入配置

在 Windows / Python 3.11 环境中：

~~~powershell
cd D:\QAgent\QAgent
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[deepseek,test]"
~~~

在同一 Python 环境中准备实验电脑已经验证可用的 QuantumX2 驱动与依赖。需要的 QuantumX2 版本必须包含 src/quantumx/calibration.py。

编辑 config/runtime.json：

- quantumx.source_dir：实际 QuantumX2 根目录。
- quantumx.hardware_config / qubits_config：实际实验配置文件，默认指向 QuantumX2/configs/hardware.json 与 qubits.json，文件必须存在。
- quantumx.driver_dir：可选，ZWserver 驱动目录；省略时使用 QuantumX2 的 3rd_libs/ZW_driver/ZWserver。
- quantumx.allowed_qubits：允许的目标编号，q1 对应 n=1。
- quantumx.parameter_files：三项实验的默认参数文件。
- result_dir / input_archive_dir：输出与输入快照目录。

运行 JSON 中的相对路径以该 JSON 所在目录为起点。实验默认参数放在 config/experiments/，可编辑 n、shots、点数、扫描范围等。模型显式传入的参数覆盖默认参数；所有输入仍经过类型、范围、目标和预算校验。

由用户在本地配置模型凭证 DEEPSEEK_API_KEY、DEEPSEEK_MODEL，按需要配置 DEEPSEEK_BASE_URL。启动器可以加载启动目录的本地凭证文件；它们不会交给模型或写入实验报告。

## 使用

~~~powershell
# 仅解析配置，不连接设备，不需要模型凭证
.\.venv\Scripts\qagent.exe check

# 默认真机对话入口
.\.venv\Scripts\qagent.exe

# 单条自然语言指令，或恢复同一配置下的对话
.\.venv\Scripts\qagent.exe -n "计算 q1 的读出中心"
.\.venv\Scripts\qagent.exe -r 对话编号

# 不经过模型，直接执行真机函数
.\.venv\Scripts\qagent.exe run readout_centers --params-file config/experiments/readout_centers.json

# 查询真实记录，不发起采集
.\.venv\Scripts\qagent.exe list
.\.venv\Scripts\qagent.exe result 实验编号
.\.venv\Scripts\qagent.exe stop 实验编号
.\.venv\Scripts\qagent.exe compare 实验编号A 实验编号B
~~~

run 命令会执行真实设备采集；check 和 describe 不连接设备。可通过 --runtime 指定另一份运行 JSON，通过 --storage 指定业务日志目录。

对话中可说“使用 q1 上一次合格读出中心候选，调整 π 脉冲幅度”。模型使用 reference_experiment_id 将候选应用到新实验配置副本。每条用户指令最多启动一个新实验，后台运行期间可查询或停止。

模型可以选择参数并引用候选，当前没有无人值守的多轮自动收敛流程。Ramsey 是围绕已有 f10 精调，不提供宽范围共振搜索。函数完成和候选质量合格不等于整项校准通过独立验证。原函数固定 update=False、show_plot=False，不写回 QuantumX2 原配置。

## 输入、输出与日志

~~~text
QAgent/
├── config/
│   ├── runtime.json
│   ├── experiments/                  # 可编辑的实验默认参数
│   └── history/
│       └── quantumx/<实验编号>/
│           ├── parameters.json       # 实际生效的参数和扫描轴
│           ├── hardware.json
│           └── qubits.json           # 包含本轮明确引用的候选
├── result/
│   └── quantumx/<实验编号>/
│       ├── iq-0000.npy / iq-0000.csv  # 完整原始 IQ，每行 I,Q
│       ├── pulse-0000.json
│       ├── acquisitions.json
│       ├── result.json               # 原函数曲线与拟合结果
│       ├── analysis.json / curve.csv
│       ├── iq.png / fit.png
│       ├── report.json
│       └── summary.html              # 双击查看
└── runs/
    └── journal.sqlite                # 对话、回执、预算、停止标志
~~~

原始 IQ 在每次采集返回后原子写入 NPY。CSV、图像和人工报告在函数结束、失败或停止核对时生成。只在实际有数据时生成对应文件，读出中心没有拟合曲线图；失败也保留已有 IQ。散点图抽样显示，NPY/CSV 保存完整数据。

退出对话在下一次采集边界停止，当前调用可能仍在运行；强制关闭进程后先查询并核对旧记录，不自动重放。路径、源码或输入配置变化后请新建对话。

详细边界见 [使用说明](docs/QUANTUMX2_USAGE.md)，验证记录见 [实现记录](docs/IMPLEMENTATION_STATUS.md)。历史版本说明保留在 Git 与旧设计文档中。

## 离线开发验证

~~~powershell
$env:QAGENT_NO_ENV_FILE = "1"
$env:LANGSMITH_TRACING = "false"
$env:LANGCHAIN_TRACING_V2 = "false"
.\.venv\Scripts\python.exe -m pytest -q
~~~

测试仅使用替身 IQ 和内存 HTTP 服务，不读取真实凭证文件、不连接设备。缺少相邻 QuantumX2 源码时，依赖该源码的集成测试会明确跳过。离线通过不能替代目标电脑的驱动、设备采集和在线模型验收。
