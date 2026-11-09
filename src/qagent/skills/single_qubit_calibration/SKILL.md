---
name: single_qubit_calibration
description: QuantumX 单比特实验选择、测量原理、参数单位和结果解读。
---

# QuantumX 单比特校准

依据 QuantumX 的 `src/quantumx/calibration.py` 中的 `SingleQubitCalibration`、`docs/single_qubit_calibration_api.md` 和 `docs/experiment_cli.md`。条目用类方法名索引。类方法参数与 QAgent 提交的 CLI 参数分别说明，实际默认值和可执行范围由运行时能力提供。

时间用秒，频率用 Hz，旋转角和脉冲相位用 rad。读出幅度、XY 与动态 Z 幅度通常为归一化 DAC 值。JPA 泵浦功率为 dBm，JPA 偏置电压为 V。IQ 的最后一维为 I、Q。CLI 的 `n` 是比特编号，`shots` 是单组采集次数，`points` 是主轴点数。多轴或多条件扫描的总采集量由 QuantumX 预检确定。具备 `quality.accepted` 的结果可供后续候选引用，表征有效性由 `quality.valid` 表示。

## sParameter · 腔谱

只施加探测脉冲，逐频点相干平均复数 IQ，以幅度最小的采样点定位候选腔共振。幅相来自同一复均值，当前结果是未归一化探测响应。

参数 `freq_scan` 为探测频率轴（Hz）。`readout_enable_z` 控制探测期间是否施加读出 Z。可选 `shots=1000`、`readoutAmp`、`readoutLen`（s）、`pumpOutput` 和 `show_plot`。查看 `resonance_frequency`、幅相曲线及 JPA 实机状态。

## jpaPumpFrequency · JPA 泵浦找频

固定探测频率并扫描泵频，以相干平均 IQ 幅度最大点作为当前条件下的候选泵频。需要支持临时 JPA 设置与恢复的后端。

参数 `pump_freqs` 和可选 `probe_freq` 用 Hz，`biasOperate` 用 V，`pumpPower` 用 dBm，另有 `shots=1000` 和读出幅度、脉宽选项。核对 `best_pump_frequency`、峰是否在扫描内和实机读回的 `best_jpa_state`。

## jpaBiasPumpPower · JPA 偏置与功率二维扫描

在固定探测频率下按偏置和泵功率逐点测量响应，用幅度峰选择候选组合。检查两维轴次序和对应的硬件读回。

参数 `bias_values` 用 V，`pump_powers` 用 dBm。两轴应严格单调或只有一点。可选 `probe_freq`、`pumpFrequency`（Hz）、`shots=1000` 及探测幅度和脉宽。主要输出 `best_bias`、`best_pump_power` 和响应矩阵。

## calReadoutTiming · ADC 到达时间与积分窗

比较有探测与零探测的原始 ADC 波形，在同一时基中找持续超过参考噪声阈值的区间。需要能返回原始采样轨迹的 `trace_reader`。

参数 `duration_s` 为采样长度，`shots=128`，`min_run=4` 为最短连续采样点数，`adc_full_scale` 用于饱和判断，`readout_enable_z` 控制读出 Z。检查 `arrival_s`、`adc_delay_s`、`integration_window_s` 及满量程信息。

## calInitialReadout · 初始读出幅度与功率

按读出幅度和可选功率测平均 IQ、噪声及前后半批漂移，筛选符合信噪比和可用饱和判据的读出条件。

参数 `amps` 为幅度轴，`powers` 为可选 dBm 轴且需要独立功率控制。默认 `shots=500`、`min_snr=1.0`，可设置 `readout_enable_z`。ADC 检查需要 `capture_duration_s`、`adc_full_scale` 和轨迹适配器。查看候选幅度、功率、SNR、漂移和 `valid`。

## spectroscopy · 比特找频

比较长驱动与匹配时窗的参考 IQ，以复均值差的对比度定位谱峰。类方法支持 `01`、`02`、`12` 跃迁。当前 CLI 执行 01 找频，输出 `best_frequency`、`best_f10` 和 `peak_snr`。峰在边缘或对比度低时应检查扫描区间。

类方法接收 `frequencies`、`amps`、`transition`、`duration_s=10e-6`、`shots=500`、`envelope="raised_cosine"`。CLI 使用 `freq_start_hz`、`freq_stop_hz`、`points` 展开频率轴，省略边界时采用当前 f10 ±15 MHz。保留 `amps`、`duration_s`、`envelope`，包络也可取 `square`。每幅度每频点含参考与驱动两组 IQ。

## calAnharmonicity · 非谐性与 f21

利用 02 双光子或 12 谱峰与已知 f10 求非谐性。02 输入频率是实际双光子驱动频率，计算 `2 × (best_frequency − f10)`。12 用 `best_frequency − f10`。

参数为 `frequencies`、`amps`、`transition="02"`，以及频谱的 `duration_s`、`shots` 和 `envelope`。输出 `anharmonicity_hz`、`f21` 和频谱质量。

## pituner · π 脉冲幅度

固定频率与门长，从零扫描驱动幅度，用 Rabi 曲线的第一个正向翻转峰选 `best_piAmp`。可用平均 IQ 主轴投影，或在有分类中心时用 P(1)。检查对比度、拟合质量及峰是否落在有效范围。

类方法用 `amps`、`shots=500` 和 `observable="iq"`。CLI 用 `amp_start=0`、`amp_stop` 和 `points` 展开至少 7 个点，省略上限时由当前 π 幅度和 DRAG/DAC 范围决定。

## calPi21 · 1→2 跃迁的 π 幅度

先用 01 π 脉冲制备 1 态，再扫描 12 驱动并从平均 IQ 拟合第一次翻转。需已知 f21 和可用 01 π。

参数 `amps` 至少 7 点并从零起扫，`duration_s` 为 12 脉宽，默认 `shots=500`。输出 `best_pi21Amp` 及 `pi21=(幅度, 秒)`。

## calc_readout_centers · 两态 IQ 中心

制备近似 0、1 态，使用匹配的脉冲时窗采集 IQ，将样本分成训练与验证，估计分类中心并用留出样本计算保真度。查看 `center0`、`center1`、`fidelity` 和验证样本数。该值包含制备条件的影响。

类方法用每态 `shots=2000`、交替块 `block_shots=200`、`validation_fraction=0.2` 和 `apply=False`。CLI 接收 `shots` 与 `validation_fraction`，固定每态一组，合计两组 IQ。

## calReadoutMatrix · 读取指派矩阵

对已制备的态分类，用留出数据形成列归一化矩阵 `M[measured,prepared]`。满足 `p_measured = M @ p_prepared`。核对条件数、列和与制备标签。

参数 `shots=3000`、`block_shots=200`、`validation_fraction=0.2`、`apply=False`。加入 `pi21=(幅度, 秒)` 和已知 f21 时增加 2 态，可得到三态矩阵。

## correct_readout · 读取概率修正

对已应用的指派矩阵做带非负和归一化约束的概率估计。该方法处理数据，使用时应复核矩阵是否对应当前制备和读出条件。

参数 `probabilities` 是按测得态排序且和为 1 的向量，`iterations=1000`。返回同维度的修正概率。

## calT1 · 能量弛豫时间

用 π 脉冲制备激发态，改变从 π 结束到读出的等待，拟合 P(1) 随时间的衰减得到 `T1`（s）。需要有效读出中心。检查有效性、拟合质量、扫描是否覆盖衰减及长延时基线。

类方法用 `delays`（s，至少 4 点）与 `shots=500`。CLI 用 `delay_start_s`、`delay_stop_s` 和 `points` 展开扫描，默认 0 到 10 μs，共 31 点，每点一组 IQ。结果用于表征。

## calT2Echo · Hahn Echo 相干时间

在两段自由演化中间插入 π 脉冲，比较相反分析相位的 P(1)，拟合对比度随总延时的衰减得到 `T2Echo`（s）。需可用门和读出中心。

类方法用总演化 `delays`（s，至少 4 点）、`shots=500`、`phase=0`（rad）。CLI 用 `delay_start_s`、`delay_stop_s`、`points` 和 `phase`，默认 0 到 10 μs 共 31 点，每点两组 IQ。结果用于表征。

## calRecovery · 实验恢复等待

扫描完整序列后的等待，比较有 π、无 π 两条曲线与最长等待参考，找后续各点均满足偏差阈值的最短等待。

参数 `waits` 为至少两个等待值（s），`shots=1000`，`tolerance=0.02` 为占据偏差。查看 `recommended_wait_s`、偏差曲线和 `valid`。

## calXYZDelay · XY 与 Z 脉冲对时

改变 Z 中点相对 Xπ 中点的时间，比较无 Z、带 Z 与 Z-only 条件，拟合重叠抑制。输出 `correction_s` 与 `z_delay_s`，后者包含现有补偿，正值将 Z 事件向后移。

类方法用 `delays`（至少 5 点）、`z_amp`、`z_duration_s`、`shots=500`。CLI 使用 `delay_start_s`、`delay_stop_s`、`points`，必须显式提供非零 `z_amp` 和正脉宽 `z_duration_s`，每点三组 IQ。使用已验证的 Z 范围。

## calPiHalf · π/2 幅度与相对相位

用两个同向 π/2 的总翻转扫描幅度，再用分析相位检查 X、Y、−Y 轴。输出 `best_piHalfAmp`、`relative_phase`、`phase_error` 与 `valid`。

类方法用 `amps`、可选 `phases`（rad）和 `shots=500`。CLI 用 `amp_start`、`amp_stop`、`points` 及 `phase_points=16`（4–64）。未给幅度边界时从当前半 π 幅度展开。采集总组数是幅度点数加三倍相位点数。

## freqtuner · Ramsey X/Y 频率精调

用两组正交分析相位构造 Ramsey 条纹，得到带符号拟合频率。频率修正为参考条纹频率减去拟合频率，输出 `best_f10`（Hz）、`correction`（Hz）和 `T2Star`（s）。需已有参考 f10、可用 π/2 和读出中心。

类方法用等间距 `delays`、`fringe_freq`、`nfftpoints` 和 `shots`。CLI 用 `delay_start_s`、`delay_stop_s`、`points`、`fringe_freq_hz`、`nfftpoints`。每点两组 IQ。条纹频率须低于延时网格的 Nyquist 频率。

## calRotationAmplitude · 有符号转角误差精调

对同向门做不同次数的重复，用 I、X180、X90、−X90 四种末端分析提取正交分量和有符号角度误差，寻找最小误差的幅度。

参数 `amps` 至少 3 点，`repetitions` 至少两个正数，`gate="pi"` 或 `"half"`，`shots=500`，`max_error_rad=0.2`。查看 `angle_error_rad`、拟合质量和相应幅度候选。

## calDRAG · DRAG 系数

对每个 alpha 和重复次数，分别做 X、Y 轴上的正反 π 对，并用末端 π/2 分析。评分兼顾高 P(1) 与两轴一致性，输出 `best_alphaDRAG`（s）。复核扫描是否包住候选。

类方法用 `alphas`（s）、`repetitions`、`shots=500`、`consistency_weight=1.0`。CLI 使用 `alpha_start_s`、`alpha_stop_s`、`points` 和 `repetitions`，缺省范围在当前 alpha 附近 ±1 ns。每个 alpha 和重复次数组合含 X、Y 两组 IQ。

## calFluxSpectrum · 静态磁通谱与甜点

在每个静态偏置稳定后执行 01 找频，并拟合频率随偏置的周期曲线。检查谱峰有效性、可用区间和换点后的读出及门参数。

参数 `biases` 至少 5 点，`frequencies`、`amps` 为内部谱轴，默认 `duration_s=10e-6`、`settle_s=1e-3`、`shots=500`。可用 `period_bounds`（与偏置同单位）和 `envelope`。输出 `sweetspot`、`period`、`fit`、`usable_bias_intervals`。

## calZDistortion · Z 拖尾与预补偿

在 Z 撤除后改变 Ramsey 采样时刻，比较有 Z 与无 Z 的相位，用静态磁通映射的单调支反推拖尾，拟合因果预补偿并复测。

参数 `delays` 至少 6 点，`z_amp`、`z_duration_s`、`analysis_time_s`，静态谱 `flux_model` 和单调区间 `flux_branch`。可选 `time_constants_s`、`shots=500`、`verify=True`、`apply=False`。检查分支可逆、波形范围和补偿前后对照。

## calDetuneT1 · 失谐期间的弛豫损失

制备激发态后施加 Z 并停留，回到工作点读出，与匹配时序的无 Z 参考比较，定位失谐损失区。

参数 `z_amps`、至少 4 个正的 `delays`、`settle_s`、`shots=500`、`loss_threshold=0.1`。可用 `flux_model` 把偏置映射到频率。查看 `extra_loss`、`loss_regions` 和各幅度对应的 `T1`。

## calReadout · 多轴读出条件优化

在不同读出设置下采集两态 IQ，逐点重估中心并用留出样本比较平均保真度。输出 `best`、`Favg` 和各点统计。

参数可选 `frequencies`、`amps`、`z_amps`、`lengths`、`windows`、`powers`。窗口为落在读出脉宽内的 `(start_s, stop_s)`，功率轴需要外部功率适配器。默认 `shots=1000`、`block_shots=200`、`validation_fraction=0.2`。多轴组合会放大采集量。

## readoutAmpFid · 读出幅度保真度

固定其余读出条件，对 `calReadout` 的读出幅度轴逐点比较验证集保真度。

参数 `amps` 为归一化幅度轴。其他选项转交 `calReadout`，包括 shots 和留出比例。结果按联合读出结构返回。

## readoutZpaFid · 读出 Z 幅度保真度

用 `calReadout` 对读出期间的 Z 幅度逐点比较验证集保真度，核对读出路径与安全幅度。

参数 `zpas` 为归一化 Z 幅度轴，其他选项转交 `calReadout`。结果按联合读出结构返回。

## calReadoutPath · 读出路径损失与泄漏

对比参考、仅 Z、完整读出三条前置路径，再统一末端读出，分离转移损失与读出诱发损失。

参数 `settle_s`、`shots=1000`、可选 `lengths`（s）。查看 `transfer_loss`、`readout_induced_loss`、布居和泄漏。

## calCavityClear · 腔清空等待

比较读出后不同等待下的 Ramsey 残余相位与参考，以容差筛选恢复等待。

参数 `waits` 和 `ramsey_delay_s` 用秒，`shots=500`，`tolerance=0.03`。核对参考和后续点是否稳定。

## calLeakage · 门序列泄漏

执行指定旋转串并用三态读出估计 2 态占据，比较不同重复次数下的泄漏。

参数 `rotations` 是 `(theta, phase)`（rad）列表，`shots=2000`、`repetitions=(1,)`。需 0、1、2 态读取中心。查看 `leakage`，有已应用三态矩阵时也可查看修正布居。

## calTuneGates · 门参数闭环精调

类方法依次检查 Ramsey 失谐、π 与半 π 转角误差和 DRAG，应用合格的实例候选并复测残差。用 `converged` 与逐轮 `history` 判断是否收敛。

必填 `pi_amps`、`half_amps`、`alphas`、`repetitions`、`ramsey_delays`。默认 `shots=500`、`max_rounds=3`。可设置 `detuning_tolerance_hz`、`angle_tolerance_rad`、`alpha_tolerance_s`、`residual_delays` 和正的 4 的倍数 `verification_repetitions`。

## sqRbClifford · 单比特随机基准

随机 Clifford 序列加恢复门，按长度拟合存活率衰减估计 EPC。交错模式插入指定门，比较参考与交错衰减。记录随机种子、批次数和误差估计。

参数 `lengths` 至少四个不同长度，默认 `num_sequences=10`、`shots=500`、`seed=0`。可选 `interleaved`、共享后端的 `spectator`、`max_error_per_clifford` 和 `max_leakage`。设置泄漏阈值需要 2 态中心。

## calRBAcceptance · 独立批次 RB 验收

用多个独立随机种子比较参考 EPC、批次间 EPC 极差和泄漏。查看每批拟合与 `passed`。

参数 `seeds` 至少两个不同种子，必填 `max_epc_spread`、`max_error_per_clifford`、`max_leakage`。其他选项转给 RB，至少提供 `lengths`。

## calCrosstalk · 驱动与读出串扰

对旁观比特施加 XY 或 Z 驱动时，用目标比特的 Ramsey 相位和对比度估计影响。读出模式在共享 probe 上加入另一读出音，比较目标 IQ 位移。

参数 `spectator` 是共享后端的另一校准实例，`amplitudes`、`duration_s`、`shots=500`、`kind="xy"`、`"z"` 或 `"readout"`。后者要求共享物理探测通道。

## apply_parameters · 应用实例参数

将已验证候选写入校准实例的生效参数，重新检查读出依赖与需要复核的项目。更换静态偏置要求当前会话关闭。

参数含 `piHalfAmp`、`z_delay_s`、`adc_delay_s`、`integration_window_s`、`readoutPower` 和允许的比特字段，如 `f10`、`f21`、`piAmp`、`alphaDRAG` 和 `readoutCenters`。QAgent 通过显式前轮结果引用传递候选。

## snapshot · 参数快照

读取当前实例中的比特、门、读出、Z 补偿与待复核状态。无参数，用于确认实验生效条件和复现。

## save_result · 结果归档

将时间戳、当前参数快照和结果转为 JSON 存档。参数为目标 `path` 和 `result` 字典。更换候选前归档可保留原测量条件。
