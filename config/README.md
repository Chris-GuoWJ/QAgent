# 实验输入

- runtime.json：QuantumX2 源码、真实硬件和比特配置路径、允许目标、预算，以及输出位置。
- experiments/*.json：用户可编辑的实验默认参数；显式工具/CLI 参数覆盖这些默认值。
- history/quantumx/<实验编号>/parameters.json：该次实际生效的参数和扫描轴。
- history/quantumx/<实验编号>/hardware.json、qubits.json：实际使用的配置快照。
- history/quantumx-inputs/<指纹>/：来源配置归档，用于可追溯与会话身份校验。

修改默认参数或来源配置后请启动新对话。history 是已执行实验的输入证据，不应通过手工编辑它来修改运行中的实验。

runtime.json 中的相对路径以该文件所在目录为起点。默认真机配置文件名是 hardware.json 和 qubits.json，不使用 _tmpl 模板自动补齐。

全体图像、IQ 和分析输出位于项目根目录 result/；对话与运行日志位于 runs/。本目录不存放模型密钥。
