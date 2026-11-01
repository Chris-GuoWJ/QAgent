# QAgent 目录与架构（0.6.0）

本轮参考本地 NVIDIA Quantum-Calibration-Agent-Blueprint 的实际目录分工：core/runner.py 管执行，core/storage.py 管存储，tools/ 管模型工具，入口负责装配，tests/unit 与 tests/integration 分开。QAgent 保留 src 包结构，并把输入、人工结果和内部数据分开。

## 项目总览

~~~text
QAgent/
├── config/                  # 实验输入与默认值
│   ├── runtime.json
│   ├── experiments/
│   └── history/             # 每次实验实际输入快照
├── result/                  # 原始 IQ、分析、PNG、人工报告
├── data/                    # 内部状态与开发产物
│   ├── state/
│   │   ├── journal.sqlite
│   │   └── locks/
│   ├── cache/
│   ├── tmp/
│   └── archive/
├── src/qagent/              # 安装包源码
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── helpers.py
│   └── conftest.py
├── docs/
│   ├── ARCHITECTURE.md
│   ├── QUANTUMX2_USAGE.md
│   ├── IMPLEMENTATION_STATUS.md
│   └── archive/             # 历史设计
├── README.md
├── pyproject.toml
└── requirements-lock.txt
~~~

## 源码分层

~~~text
src/qagent/
├── app.py                   # 读取配置，装配存储、实验服务和模型配置
├── cli.py                   # 保留原 qagent.exe 的稳定入口
├── __main__.py              # python -m qagent
├── agent/
│   ├── loop.py              # 模型 → 工具 → 观察结果的对话循环
│   ├── prompts.py           # 系统提示与能力边界
│   ├── session.py           # 会话操作、单条指令限制、退出控制
│   ├── history.py           # 持久化对话
│   ├── model.py             # DeepSeek 客户端
│   └── credentials.py       # 应用启动时加载本地凭证
├── core/
│   ├── runner.py            # 实验执行、预算、回执、停止、候选引用
│   ├── storage.py           # SQLite、原子文件写入与锁
│   ├── config.py            # JSON 输入读取边界
│   └── models.py            # 严格数据模型基类
├── experiments/
│   ├── registry.py          # 实验名 → QuantumX 函数的固定映射
│   └── parameters.py        # 输入参数、单位和范围校验
├── tools/
│   └── experiments.py       # 模型能调用的实验、查询和停止工具
├── integrations/quantumx/
│   ├── library.py           # 加载指定 QuantumX 源码和 ZwBackend
│   └── recording_backend.py # 包裹后端，逐次保存 IQ 和 PulseIR
├── reporting/
│   ├── serialization.py     # NumPy 序列化、指标与质量摘要
│   └── presentation.py      # CSV、PNG、HTML 人工报告
└── interfaces/
    ├── cli.py               # 命令参数解析和分发
    └── terminal.py          # 自然语言终端、进度显示
~~~

建议阅读顺序：app.py → agent/loop.py → tools/experiments.py → core/runner.py → integrations/quantumx/recording_backend.py → reporting/presentation.py。

## 调用链

~~~mermaid
flowchart LR
    U[终端或命令] --> I[interfaces]
    I --> A[app 装配]
    A --> M[agent 对话循环]
    M --> T[tools 实验工具]
    T --> R[core.runner]
    C[config 输入] --> R
    R --> Q[QuantumX 原函数]
    Q --> B[RecordingBackend 与 ZwBackend]
    B --> D[result 原始 IQ]
    Q --> P[reporting]
    P --> O[result 分析 图像 HTML]
    R --> S[data/state 日志与锁]
~~~

命令行直接 run 时也使用同一个 core.runner，不经过对话模型。硬件能力、参数判据和自动调参范围未因目录整理扩大。

## 与 Blueprint 的对应

| Blueprint 分工 | QAgent 对应 |
| --- | --- |
| core/runner.py、core/models.py | core/runner.py、experiments/parameters.py、core/models.py |
| core/storage.py、data/ | core/storage.py、data/state/ |
| tools/lab_tool.py 等 | tools/experiments.py |
| prompt.py、CLI 中的 Agent 装配 | agent/prompts.py、agent/loop.py、app.py |
| scripts/ 下的实验实现 | 外部 QuantumX2 公共函数，加 experiments/ 固定注册 |
| tests/unit、tests/integration | 同名测试分层 |

实验输出继续采用用户指定的 result/，原始 IQ 保留 NPY/CSV，人工页面保持 summary.html。QuantumX2 与 Blueprint 原库均未修改。

## 旧目录迁移

旧 runs/、build/、pytest 缓存被保留到 data/ 的归档或缓存子目录，不删除历史实验。主 SQLite 数据库使用备份接口复制并验证，再归档旧目录；整理时检查了 1032 个旧锁文件，未发现活动锁。

主数据库迁移后保留 runs 4 条、events 45 条、chat_sessions 5 条、chat_messages 58 条，完整性检查为 ok。这些是已有历史表的记录数，不代表新增真机实验。

使用新配置启动即可使用 data/state。旧命令若显式指定 --storage runs，会按用户指定的位置创建目录；建议移除此覆盖，或改成 --storage data/state。旧终端需重启。更早示例版本的实验文件可在 data/archive/runs-before-v0.6 中人工查看，不能自动变成真机任务。
