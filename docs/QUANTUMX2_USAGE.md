# QuantumX 真机接入（0.6.0）

本版按用户最新规划，仅提供真机功能入口。QAgent 调用 QuantumX2 的 calc_readout_centers、pituner、freqtuner，并显式注入 RecordingBackend 包裹的 ZwBackend。没有产品级模拟模式或缺配置后的模拟回退。

## 装配与数据流

~~~mermaid
flowchart LR
    U[用户自然语言] --> A[ChatAgent]
    A --> T[受控实验工具]
    C[config 默认参数和快照] --> S[QuantumXService]
    T --> S
    S --> F[QuantumX 原函数]
    F --> R[RecordingBackend]
    R --> B[ZwBackend 真机采集]
    B --> R
    R --> D[result 原始 IQ 和 PulseIR]
    F --> N[QuantumX 分类和数值拟合]
    N --> O[result 分析 图像 HTML]
    O --> A
~~~

config/runtime.json 是默认启动配置；所有相对路径以该 JSON 所在目录为起点。config/experiments 中的默认参数按实验类型加载；工具或 CLI 显式参数优先。实际输入归档在 config/history/quantumx/<编号>/，候选仅应用到新实验副本。

后端在实际执行时创建，读取本次硬件和比特配置快照，并使用可选 driver_dir。导入配置、列出能力和 check 不连接设备。加载 QuantumX 源码和后端类时禁止写入其字节码缓存，不修改该库原文件。

## 命令

默认 qagent.exe 等价于使用 config/runtime.json 进入 chat。旧 start/resume/quantumx demo 示例命令已移除。

~~~powershell
.\.venv\Scripts\qagent.exe check
.\.venv\Scripts\qagent.exe describe
.\.venv\Scripts\qagent.exe
.\.venv\Scripts\qagent.exe run pi_amplitude --params-file config/experiments/pi_amplitude.json
.\.venv\Scripts\qagent.exe result 实验编号
.\.venv\Scripts\qagent.exe list
.\.venv\Scripts\qagent.exe stop 实验编号
~~~

run 直接执行真机；自然语言对话也能调度同一服务。查询和比较不采集数据。内部日志默认放在 data/state/，全局 --storage 可以覆盖该位置，--runtime 指定输入配置；后者也可以放在子命令后。

## 能力边界

模型可以选择扫描范围、点数、shots 和 reference_experiment_id，受到参数校验及每会话预算限制。每条指令最多新建一个实验，没有持续自动多轮调整直到收敛的执行器。一次函数调用内部的扫描和拟合由 QuantumX 完成，不是语言模型计算。

默认注册表只支持读出中心、π 幅度、Ramsey X/Y。Ramsey 是已有频率附近精调，不能代替宽频谱找峰。quality.accepted 是候选质量检查，completed 是函数完成，都不代表整项校准独立验证成功。

update=False 和 show_plot=False 固定传入，图像由 QAgent 从保存的数据生成。预算按计划预留，失败和中断保留预留成本；actual_measurements 仅表示已保存有效 IQ 的 shots，不推断失败调用内部的测量状态。

## 人工查看

result/quantumx/<编号>/summary.html 可直接在浏览器打开。它链接：

- 完整原始 IQ 的 NPY 与 CSV；NPY 为实数 (shots,2)，I/Q 两列。
- 扫描标签和 PulseIR。
- 原函数 result.json、分析 analysis.json、曲线 curve.csv。
- iq.png 散点图，Rabi/Ramsey 的 fit.png。
- 参数快照和 report.json。

无返回结果时不生成 result.json 或拟合图。失败、中断仍生成已有 IQ 的预览和状态页。NPY 逐次原子保存，CSV/图像/HTML 在函数结束时生成，导出失败单独标记 export_error_type，不把已完成采集伪装为未执行。

## 会话与恢复

代码架构见 [ARCHITECTURE.md](ARCHITECTURE.md)。agent/ 管对话，tools/ 管工具，core/ 管执行与存储，integrations/ 管 QuantumX 适配，reporting/ 管人工结果。

输入、源码、驱动路径、数据来源参与会话身份校验；变化后需新会话。工具回执丢失时只查找已有操作，不自动重放。进程退出会在下一次采集前停止；强制关闭后状态可能仍 queued/running，查询后可用停止操作核对并结束旧记录。

没有自动续跑真机函数的 resume 命令，以免重复未知的采集。历史运行数据库不被删除，旧版本对话与模拟任务不转成真机任务。

## 验证

tests/helpers.py 中的 IQ 替身仅用于离线测试，不在安装包或 CLI 注册为后端。默认真机工厂通过替换类的边界测试验证快照和驱动参数传递；三个原函数、原始数据、PNG 和 HTML 使用替身完成验证。实际设备执行及在线模型决策需要在实验电脑验证。
