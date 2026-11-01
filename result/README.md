# 实验输出

每项实验写入 `quantumx/<实验编号>/`。双击其中的 `summary.html` 查看状态、参数、分析和图像。

- `iq-*.npy`：完整原始逐 shot IQ，形状为 `(shots, 2)`，列顺序是 I、Q。
- `iq-*.csv`：相同 IQ 的文本形式，便于人工查看。
- `acquisitions.json` / `pulse-*.json`：采集标签和实际 PulseIR。
- `result.json`：QuantumX 原函数的完整结果。
- `analysis.json` / `curve.csv`：分析指标和拟合曲线。
- `iq.png` / `fit.png`：IQ 散点图和扫描/拟合图。
- `report.json` / `summary.html`：机器可读报告和人工查看页面。

失败、中断也保存已有 IQ 和状态，未产生的拟合结果不会伪造。对应输入快照位于项目根目录 `config/history/`。业务日志与对话保存在 `runs/`。
