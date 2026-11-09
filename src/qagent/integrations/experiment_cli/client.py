"""Launch the configured interpreter and exchange CLI protocol messages."""

import os
import subprocess

from qagent.integrations.experiment_cli.protocol import decode_object


class ExperimentCLI:
    def __init__(self, settings):
        settings.check_paths()
        self.settings = settings

    def _command(self, name, args):
        return [str(self.settings.python_executable), "-s", "-u", "-X", "utf8",
                str(self.settings.entrypoint), name, *map(str, args)]

    def _options(self):
        # Pass only host/driver essentials. In particular, do not inherit
        # model credentials or Python package search paths from QAgent.
        host_variables = {
            "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
            "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
        }
        env = {key: value for key, value in os.environ.items() if key.upper() in host_variables}
        return dict(cwd=self.settings.working_directory, env=env, shell=False,
                    stdin=subprocess.DEVNULL, encoding="utf-8",
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                    start_new_session=os.name != "nt")

    def query(self, name, *args, timeout=None):
        try:
            result = subprocess.run(self._command(name, args), **self._options(),
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=timeout or self.settings.command_timeout_seconds)
        except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"实验 CLI {name} 无法返回响应（{type(exc).__name__}）") from None
        if result.returncode:
            raise ValueError(f"实验 CLI {name} 返回退出码 {result.returncode}")
        return decode_object(result.stdout)

    def run(self, request, stderr):
        return subprocess.Popen(self._command("run", ("--request", request)),
                                **self._options(), stdout=subprocess.PIPE, stderr=stderr)
