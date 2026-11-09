"""Versioned messages exchanged with the experiment process."""

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from qagent.core.models import Model
from qagent.experiments.parameters import ExperimentLimits


class ExperimentCLISettings(ExperimentLimits):
    protocol_version: Literal["1"] = "1"
    python_executable: Path
    entrypoint: Path
    working_directory: Path
    output_root: Path
    heartbeat_interval_seconds: float = Field(default=5, gt=0, le=60)
    command_timeout_seconds: float = Field(default=10, gt=0, le=60)
    start_timeout_seconds: float = Field(default=2, gt=0, le=30)

    def check_paths(self):
        for path in (self.python_executable, self.entrypoint):
            if not path.is_file():
                raise ValueError(f"找不到实验 CLI 文件：{path}")
        if not self.working_directory.is_dir():
            raise ValueError(f"找不到实验 CLI 工作目录：{self.working_directory}")


TERMINAL_STATES = {"completed", "failed", "cancelled"}
Counter = Annotated[int, Field(strict=True, ge=0)]


class Progress(Model):
    model_config = ConfigDict(extra="ignore")
    phase: Literal["validating", "waiting_device", "initializing",
                   "acquiring", "fitting", "exporting"] | None = None
    scan_points_done: Counter | None = None
    scan_points_total: Counter | None = None
    iq_groups_done: Counter | None = None
    iq_groups_total: Counter | None = None
    shots_done: Counter | None = None


class Event(Progress):
    protocol_version: Literal["1"]
    experiment_id: str
    seq: Annotated[int, Field(strict=True, ge=1)]
    timestamp: datetime
    event: Literal["started", "progress", "heartbeat", "completed", "failed", "cancelled"]
    result_file: str | None = None
    raw_data_dir: str | None = None
    plot_dir: str | None = None


def decode_object(value):
    def invalid_constant(name):
        raise ValueError(f"实验 CLI JSON 含有无效数值 {name}")

    data = json.loads(value, parse_constant=invalid_constant)
    if not isinstance(data, dict):
        raise ValueError("实验 CLI 必须返回 JSON 对象")
    return data


def check_identity(data, experiment_id):
    if data.get("protocol_version") != "1" or data.get("experiment_id") != experiment_id:
        raise ValueError("实验 CLI 返回的协议版本或实验编号不匹配")


def checked_path(directory, reference):
    if not isinstance(reference, str):
        raise ValueError("实验产物路径缺失")
    path = Path(reference)
    resolved = path.resolve()
    if not path.is_absolute() or not resolved.is_relative_to(directory):
        raise ValueError("实验产物必须位于当前任务目录内")
    for candidate in (path, resolved):
        name = candidate.name.casefold()
        if name == ".env" or name.startswith(".env.") or name.endswith(".env"):
            raise ValueError("实验产物不能引用凭证文件")
    return resolved
