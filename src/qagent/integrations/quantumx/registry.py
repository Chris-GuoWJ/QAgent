"""An explicit registry, not model-controlled Python imports."""

from dataclasses import dataclass

from .parameters import RabiParameters, RamseyParameters, ReadoutCentersParameters


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


def get_definition(name):
    if name not in EXPERIMENTS:
        raise ValueError("未知 QuantumX 实验，只支持 " + ", ".join(EXPERIMENTS))
    return EXPERIMENTS[name]
