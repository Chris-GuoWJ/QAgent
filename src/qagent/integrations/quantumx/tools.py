"""Narrow LangChain tools for the explicit QuantumX registry."""

from langchain_core.tools import tool

from .parameters import RabiParameters, RamseyParameters, ReadoutCentersParameters


START_TOOLS = {"measure_readout_centers", "tune_pi_amplitude", "tune_frequency_ramsey"}


def create_tools(operations, call_id):
    service = operations.quantumx
    session_id = operations.history.session_id

    def start(name, arguments):
        if operations.closing:
            raise ValueError("会话正在退出")
        if operations._started_this_turn:
            raise ValueError("本条指令已创建一个实验，请先查询结果；下一条指令可引用候选结果继续")
        receipt = service.start(name, arguments, session_id, call_id())
        operations._started_this_turn = True
        return receipt

    @tool
    def describe_quantumx_experiments() -> dict:
        """List supported hardware experiments, input defaults, parameter schemas and target indices."""
        return service.describe()

    @tool(args_schema=ReadoutCentersParameters)
    def measure_readout_centers(**arguments) -> dict:
        """Run QuantumX calc_readout_centers in the background with raw IQ storage and held-out fidelity."""
        return start("readout_centers", arguments)

    @tool(args_schema=RabiParameters)
    def tune_pi_amplitude(**arguments) -> dict:
        """Run QuantumX pituner in the background. Optionally reference a completed experiment's candidate snapshot."""
        return start("pi_amplitude", arguments)

    @tool(args_schema=RamseyParameters)
    def tune_frequency_ramsey(**arguments) -> dict:
        """Run QuantumX freqtuner Ramsey X/Y fine tuning around configured f10, NOT broad spectroscopy. Background execution."""
        return start("ramsey_frequency", arguments)

    @tool
    def get_experiment_result(experiment_id: str) -> dict:
        """Read a QuantumX experiment's persisted status, metrics, quality and artifact references; never starts experiments."""
        return service.public_result(experiment_id)

    @tool
    def list_experiment_results() -> dict:
        """List existing QuantumX experiments for this configuration; does not run or resume anything."""
        return service.list_results()

    @tool
    def compare_experiment_results(first: str, second: str) -> dict:
        """Compare metrics for two completed QuantumX experiments of the same target and type."""
        return service.compare(first, second)

    @tool
    def stop_quantumx_experiment(experiment_id: str) -> dict:
        """Persist a stop request for a QuantumX experiment. Stops further IQ acquisitions at the next call boundary."""
        return service.stop(experiment_id)

    return [describe_quantumx_experiments, measure_readout_centers, tune_pi_amplitude,
            tune_frequency_ramsey, get_experiment_result, list_experiment_results,
            compare_experiment_results, stop_quantumx_experiment]
