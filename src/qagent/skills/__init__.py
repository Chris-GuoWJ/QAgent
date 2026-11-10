"""Knowledge resources loaded by QAgent tools."""

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=1)
def single_qubit_workflow():
    resource = files(__package__).joinpath("single-qubit-workflow", "SKILL.md")
    return resource.read_text(encoding="utf-8").split("\n---\n", maxsplit=1)[-1].strip()


@lru_cache(maxsize=1)
def calibration_entries():
    resource = files(__package__).joinpath("single_qubit_calibration", "SKILL.md")
    sections = resource.read_text(encoding="utf-8").split("\n## ")[1:]
    entries = {}
    for section in sections:
        heading, _, body = section.partition("\n")
        method, title = heading.split(" · ", maxsplit=1)
        entries[method] = {"method": method, "title": title, "guide": body.strip()}
    return entries


def calibration_skill(method, capabilities):
    entries = calibration_entries()
    available = {item.get("method", item.get("function")): item for item in capabilities["experiments"]}
    helpers = {"apply_parameters", "correct_readout", "snapshot", "save_result"}
    if method is None:
        return {"methods": [
            {"method": name, "title": entry["title"], "runnable": name in available,
             "kind": "helper" if name in helpers else "experiment"}
            for name, entry in entries.items()
        ]}
    if method not in entries:
        raise ValueError("未知校准方法，请先查询 skill 的函数目录")
    return {**entries[method], "runnable": method in available,
            "kind": "helper" if method in helpers else "experiment",
            "execution": available.get(method)}
