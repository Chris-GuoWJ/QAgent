"""Separate operation journal, experiment inputs and experiment outputs."""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .config import read_json_config


class RunBusy(RuntimeError):
    pass


class Store:
    def __init__(self, root, *, result_root=None, config_root=None):
        self.root = Path(root).resolve()
        self.result_root = Path(result_root or self.root.parent / "result").resolve()
        self.config_root = Path(config_root or self.root.parent / "config" / "history").resolve()
        for path in (self.root, self.result_root, self.config_root):
            path.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "journal.sqlite"

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _path(root, reference):
        path = (root / reference).resolve()
        if not path.is_relative_to(root):
            raise ValueError("文件引用必须位于对应的 config/result 目录内")
        return path

    @staticmethod
    def _write(root, namespace, name, data):
        path = Store._path(root, str(Path(namespace) / name))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path.relative_to(root).as_posix()

    def write_artifact(self, namespace, name, data):
        return self._write(self.result_root, namespace, name, data)

    def read_artifact(self, reference):
        return read_json_config(self._path(self.result_root, reference))

    def write_input(self, namespace, name, data):
        return self._write(self.config_root, namespace, name, data)

    def read_input(self, reference):
        return read_json_config(self._path(self.config_root, reference))

    @contextmanager
    def run_lock(self, name):
        from hashlib import sha256

        path = self.root / (sha256(name.encode()).hexdigest() + ".lock")
        handle = path.open("a+b")
        try:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RunBusy("该操作已有执行器，请先查询状态") from exc
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
