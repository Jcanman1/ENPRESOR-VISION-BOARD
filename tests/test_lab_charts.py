import os
import sys
import csv
import pytest

dash = pytest.importorskip("dash")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks
import autoconnect


def setup_app(monkeypatch, tmp_path):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    monkeypatch.setattr(callbacks.hourly_data_saving, "EXPORT_DIR", str(tmp_path))
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    return app


def create_log(tmp_path):
    machine_dir = tmp_path / "1"
    machine_dir.mkdir(parents=True, exist_ok=True)
    path = machine_dir / "Lab_Test_sample.csv"
    fieldnames = ["timestamp", "objects_per_min"] + [f"counter_{i}" for i in range(1, 13)]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i in range(3):
            row = {"timestamp": f"2025-01-01T00:00:0{i}", "objects_per_min": 60}
            for j in range(1, 13):
                row[f"counter_{j}"] = 1 if j == 1 else 0
            writer.writerow(row)
    return path


def test_update_section_5_2_lab_reads_log(monkeypatch, tmp_path):
    app = setup_app(monkeypatch, tmp_path)
    create_log(tmp_path)
    func = app.callback_map["section-5-2.children"]["callback"]

    callbacks.previous_counter_values = [0] * 12
    callbacks.threshold_settings = {}

    res = func.__wrapped__(0, "main", {}, {}, "en", {"connected": False}, {"mode": "lab"}, {"machine_id": 1})

    assert callbacks.previous_counter_values[0] == 3
    bar = res.children[1]
    assert bar.figure.data[0].y[0] == 3


def test_update_section_5_1_lab_reads_log(monkeypatch, tmp_path):
    app = setup_app(monkeypatch, tmp_path)
    create_log(tmp_path)
    func = app.callback_map["section-5-1.children"]["callback"]

    result = func.__wrapped__(0, "main", {}, {}, "en", {"connected": False}, {"mode": "lab"}, {"machine_id": 1}, {"unit": "lb"}, "objects")

    graph = result.children[1]
    assert list(graph.figure.data[0].y) == [1.0, 2.0, 3.0]
