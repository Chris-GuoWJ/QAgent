"""An explicit registry, not model-controlled Python imports."""

from dataclasses import dataclass

from qagent.experiments.parameters import (
    DelayParameters, DragParameters, EchoParameters, PiHalfParameters, RabiParameters,
    RamseyParameters, ReadoutCentersParameters, SpectroscopyParameters, XYZDelayParameters,
)


@dataclass(frozen=True)
class ExperimentDefinition:
    name: str
    function_name: str
    description: str
    parameters: type

    def measurement_cost(self, params):
        points = 1 if self.name == "readout_centers" else params.points
        series = 1 if self.name == "pi_amplitude" else 2
        return points * series * params.shots


EXPERIMENTS = {
    item.name: item for item in (
        ExperimentDefinition("readout_centers", "calc_readout_centers", "计算读出中心和留出数据保真度", ReadoutCentersParameters),
        ExperimentDefinition("pi_amplitude", "pituner", "Amplitude-Rabi 扫描并拟合 pi 脉冲幅度", RabiParameters),
        ExperimentDefinition("ramsey_frequency", "freqtuner", "Ramsey X/Y 精调已有参考频率，非宽范围频谱搜索", RamseyParameters),
    )
}


CLI_EXPERIMENTS = EXPERIMENTS | {
    item.name: item for item in (
        ExperimentDefinition("qubit_spectroscopy", "spectroscopy", "对照长驱动和参考 IQ，扫描 0→1 跃迁", SpectroscopyParameters),
        ExperimentDefinition("t1", "calT1", "扫描 π 后等待时间并拟合能量弛豫 T1", DelayParameters),
        ExperimentDefinition("t2_echo", "calT2Echo", "Hahn Echo 双分析相位对照，拟合回波相干时间", EchoParameters),
        ExperimentDefinition("xy_z_delay", "calXYZDelay", "对照三种脉冲条件定位 XY 与 Z 的相对时序", XYZDelayParameters),
        ExperimentDefinition("pi_half_amplitude", "calPiHalf", "用同向半 π 对校准幅度并检查 X、Y、−Y 相对相位", PiHalfParameters),
        ExperimentDefinition("drag_alpha", "calDRAG", "用 X/Y 正反旋转串扫描 DRAG 系数", DragParameters),
    )
}


def get_definition(name, *, definitions=EXPERIMENTS):
    definition = definitions.get(name)
    if definition is None:
        definition = next((item for item in definitions.values() if item.function_name == name), None)
    if definition is None:
        raise ValueError("未知 QuantumX 实验：" + name)
    return definition
