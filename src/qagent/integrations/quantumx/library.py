"""Load one selected QuantumX tree without writing into that tree."""

import hashlib
import importlib
from pathlib import Path
import sys
import threading


_IMPORT_LOCK = threading.Lock()


def load_library(source_dir):
    source = (Path(source_dir).resolve() / "src").resolve()
    package = source / "quantumx"
    if not (package / "calibration.py").is_file():
        raise ValueError("QuantumX source_dir must contain src/quantumx/calibration.py")
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(path.read_bytes())
    fingerprint = digest.hexdigest()
    with _IMPORT_LOCK:
        existing = sys.modules.get("quantumx")
        if existing is not None:
            if Path(existing.__file__).resolve().parent != package:
                raise ValueError("当前进程已加载另一个 quantumx，请用新进程加载选定代码库")
            recorded = getattr(existing, "_qagent_source_fingerprint", None)
            if recorded is not None and recorded != fingerprint:
                raise ValueError("QuantumX 源码已变化，请重启后继续")
        previous = sys.dont_write_bytecode
        added = str(source) not in sys.path
        try:
            sys.dont_write_bytecode = True
            if added:
                sys.path.insert(0, str(source))
            module = importlib.import_module("quantumx.calibration")
            sys.modules["quantumx"]._qagent_source_fingerprint = fingerprint
            config = importlib.import_module("quantumx.config")
        finally:
            sys.dont_write_bytecode = previous
            if added:
                sys.path.remove(str(source))
    return module, config, fingerprint
