"""Human-readable outputs from measured IQ and QuantumX numerical results."""
import csv
import html
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure


def _text(path, content):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _figure(path, draw):
    figure = Figure(figsize=(8, 5), layout="constrained")
    FigureCanvasAgg(figure)
    try:
        draw(figure.subplots())
        temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        try:
            figure.savefig(temporary, format="png", dpi=140)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    finally:
        figure.clear()


def render_experiment(store, report):
    namespace = report["artifact_directory"]
    directory = store.result_root / namespace
    records = report.get("iq_records", [])
    groups, rows = {}, []
    for record in records:
        raw = np.load(store._path(store.result_root, record["data_ref"]), allow_pickle=False)
        data_path = Path(record["data_ref"])
        csv_name = data_path.with_suffix(".csv").name
        content = io.StringIO()
        np.savetxt(content, raw, delimiter=",", header="I,Q", comments="", fmt="%.17g")
        if not (directory / csv_name).exists():
            _text(directory / csv_name, content.getvalue())
        state = record.get("labels", {}).get("state")
        key = "state " + str(state) if state is not None else "IQ"
        groups.setdefault(key, []).append(raw)
        labels = html.escape(json.dumps(record["labels"], ensure_ascii=False))
        rows.append('<tr><td>' + str(record["call_index"]) + '</td><td>' + labels +
                    '</td><td>' + str(record["shots"]) + '</td><td><a href="' +
                    html.escape(data_path.name) + '">NPY</a> · <a href="' + html.escape(csv_name) +
                    '">CSV</a></td></tr>')
    if groups and not (directory / "iq.png").exists():
        def draw_iq(ax):
            for key, arrays in groups.items():
                raw = np.concatenate(arrays)
                preview = raw[::max(1, (len(raw) + 1999) // 2000)]
                ax.scatter(preview[:, 0], preview[:, 1], s=5, alpha=.45, label=key)
            ax.set(xlabel="I (a.u.)", ylabel="Q (a.u.)", title="Raw IQ preview")
            ax.legend()
            ax.grid(alpha=.2)
        _figure(directory / "iq.png", draw_iq)
    analysis = {key: report.get(key) for key in ("experiment_id", "experiment_type", "n", "data_origin",
                                               "status", "metrics", "quality", "warnings", "error_type")}
    store.write_artifact(namespace, "analysis.json", analysis)
    result = store.read_artifact(report["result_ref"]) if report.get("result_ref") else None
    name = report["experiment_type"]
    if result and name in {"pi_amplitude", "ramsey_frequency"}:
        stream = io.StringIO()
        writer = csv.writer(stream)
        if name == "pi_amplitude":
            x = result["amps"]
            writer.writerow(["amplitude_normalized", "p1", "p1_fitted"])
            writer.writerows(zip(x, result["p1"], result["p1_fitted"], strict=True))
        else:
            x = result["delays"]
            writer.writerow(["delay_s", "p1_x", "p1_y", "p1_x_fitted", "p1_y_fitted"])
            writer.writerows(zip(x, result["p1_x"], result["p1_y"], result["p1_x_fitted"], result["p1_y_fitted"], strict=True))
        if not (directory / "curve.csv").exists():
            _text(directory / "curve.csv", stream.getvalue())

        def draw_fit(ax):
            if name == "pi_amplitude":
                ax.plot(x, result["p1"], "o", ms=4, label="Measured")
                ax.plot(x, result["p1_fitted"], "-", label="QuantumX fit")
                ax.axvline(result["best_piAmp"], color="gray", ls="--", label="Candidate pi amplitude")
                ax.set(xlabel="Drive amplitude (normalized)", title="Amplitude Rabi q" + str(report["n"]))
            else:
                times = np.asarray(x) * 1e9
                for series, color in (("x", "C0"), ("y", "C1")):
                    ax.plot(times, result["p1_" + series], "o", color=color, ms=4, label=series.upper() + " measured")
                    ax.plot(times, result["p1_" + series + "_fitted"], color=color, label=series.upper() + " fit")
                ax.set(xlabel="Delay (ns)", title="Ramsey X/Y q" + str(report["n"]))
            ax.set_ylabel("IQ-classified P(1)")
            ax.legend()
            ax.grid(alpha=.2)
        if not (directory / "fit.png").exists():
            _figure(directory / "fit.png", draw_fit)
    title = {"readout_centers": "读出中心", "pi_amplitude": "π 脉冲幅度", "ramsey_frequency": "Ramsey 频率精调"}[name]
    origin = "真机后端" if report["data_origin"] == "hardware" else "测试替身（非真机数据）"
    timestamp = datetime.fromtimestamp(report["created_at"], timezone.utc).astimezone().isoformat(timespec="seconds")
    metrics = html.escape(json.dumps(report.get("metrics") or {}, ensure_ascii=False, indent=2))
    parameters = html.escape(json.dumps(report["parameters"], ensure_ascii=False, indent=2))
    accepted = bool((report.get("quality") or {}).get("accepted"))
    quality = "质量检查合格的候选；整项校准尚未独立验证" if accepted else "无合格候选，或分析未完成"
    images = "".join('<figure><img src="' + p + '" alt="' + p + '"></figure>'
                     for p in ("iq.png", "fit.png") if (directory / p).exists())
    links = '<a href="report.json">完整报告</a> · <a href="analysis.json">分析 JSON</a>'
    if (directory / "acquisitions.json").exists():
        links += ' · <a href="acquisitions.json">采集清单</a>'
    if result:
        links += ' · <a href="result.json">原函数结果</a>'
    if (directory / "curve.csv").exists():
        links += ' · <a href="curve.csv">扫描/拟合 CSV</a>'
    input_path = store.config_root / namespace
    input_link = html.escape(Path(os.path.relpath(input_path / "parameters.json", directory)).as_posix(), quote=True)
    links += ' · <a href="' + input_link + '">输入参数快照</a>'
    error = '<p>错误类型：' + html.escape(report["error_type"]) + '</p>' if report.get("error_type") else ""
    page = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>q{report['n']} {title} · {report['experiment_id']}</title>
<style>body{{font:16px system-ui;max-width:1060px;margin:32px auto;padding:0 20px;color:#172638;background:#f8fafc}}
h1{{font-size:26px}}.card{{background:white;border:1px solid #dbe3ec;border-radius:10px;padding:20px;margin:16px 0}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere}}img{{max-width:100%;height:auto}}table{{border-collapse:collapse;width:100%}}
td,th{{padding:9px;text-align:left;border-bottom:1px solid #e2e8f0}}a{{color:#1766ad}}figure{{margin:12px 0}}</style>
<h1>q{report['n']} · {title}</h1><p>{timestamp} · {origin}</p>
<div class="card"><b>状态：{html.escape(report['status'])}</b><p>{quality}</p>{error}
<p>实验编号：{report['experiment_id']}</p><p>保存 IQ：{len(records)} 次采集 / {report['actual_measurements']} shots；
预算预留：{report['reserved_measurements']} shots</p><p>{links}</p></div>
<div class="card"><h2>分析结果</h2><pre>{metrics}</pre></div>
<div class="card"><h2>图像</h2>{images}<p>散点图最多显示每组 2000 个点，NPY/CSV 保存完整原始 IQ。</p></div>
<div class="card"><h2>实验输入</h2><pre>{parameters}</pre><p>快照目录：{html.escape(str(input_path))}</p></div>
<div class="card"><h2>原始 IQ 文件</h2><table><tr><th>采集</th><th>扫描标签</th><th>shots</th><th>数据</th></tr>
{''.join(rows)}</table></div></html>"""
    _text(directory / "summary.html", page)
