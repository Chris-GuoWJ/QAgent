"""Narrow LangChain tools for the explicit QuantumX registry."""

from langchain_core.tools import StructuredTool, tool

from qagent.experiments.parameters import RabiParameters, RamseyParameters, ReadoutCentersParameters
from qagent.experiments.registry import CLI_EXPERIMENTS
from qagent.skills import calibration_skill, single_qubit_workflow


LEGACY_START_TOOLS = {
    "measure_readout_centers": "calc_readout_centers",
    "tune_pi_amplitude": "pituner",
    "tune_frequency_ramsey": "freqtuner",
}
START_TOOLS = set(LEGACY_START_TOOLS) | {definition.function_name for definition in CLI_EXPERIMENTS.values()}


def _parameter_schema(parameters):
    # Let the service merge the executor's defaults before validating.
    # A Pydantic class passed to LangChain can fill omitted model arguments.
    schema = parameters.model_json_schema()
    for field in schema["properties"].values():
        field.pop("default", None)
    return schema


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

    @tool
    def get_calibration_skill(method: str | None = None) -> dict:
        """Look up a QuantumX calibration function's principle, parameters and result. Omit method to list the full skill and executable capabilities."""
        return calibration_skill(method, service.describe())

    @tool
    def get_single_qubit_workflow() -> dict:
        """Read the full single-qubit calibration order after VNA, stage prerequisites, acceptance criteria and how to diagnose what to do next. Does not start measurements."""
        return {"guide": single_qubit_workflow(), "registered_experiments": registered_experiments}

    @tool(args_schema=_parameter_schema(ReadoutCentersParameters))
    def measure_readout_centers(**arguments) -> dict:
        """Run QuantumX calc_readout_centers in the background with raw IQ storage and held-out fidelity."""
        return start("readout_centers", arguments)

    @tool(args_schema=_parameter_schema(RabiParameters))
    def tune_pi_amplitude(**arguments) -> dict:
        """Run QuantumX pituner in the background. Optionally reference a completed experiment's candidate snapshot."""
        return start("pi_amplitude", arguments)

    @tool(args_schema=_parameter_schema(RamseyParameters))
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

    def cli_tool(capability):
        definition = CLI_EXPERIMENTS[capability["name"]]

        def execute(**arguments):
            return start(definition.name, arguments)

        return StructuredTool.from_function(
            execute, name=definition.function_name, args_schema=_parameter_schema(definition.parameters),
            description=definition.description + "。明确要求执行时提交后台实验。原理和参数可查询 get_calibration_skill。",
        )

    capabilities = service.describe()
    if capabilities.get("backend") == "experiment_cli":
        starts = [cli_tool(item) for item in capabilities["experiments"]]
        registered_experiments = {start_tool.name: start_tool.name for start_tool in starts}
    else:
        starts = [measure_readout_centers, tune_pi_amplitude, tune_frequency_ramsey]
        registered_experiments = {LEGACY_START_TOOLS[start_tool.name]: start_tool.name for start_tool in starts}
    return [describe_quantumx_experiments, get_calibration_skill, get_single_qubit_workflow, *starts,
            get_experiment_result, list_experiment_results, compare_experiment_results, stop_quantumx_experiment]
