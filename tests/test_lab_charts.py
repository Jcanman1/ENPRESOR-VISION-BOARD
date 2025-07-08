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


def create_lab_metrics(tmp_path):
    machine_dir = tmp_path / "1"
    machine_dir.mkdir(parents=True, exist_ok=True)
    path = machine_dir / "Lab_Test_sample.csv"
    fieldnames = [
        "timestamp",
        "capacity",
        "accepts",
        "rejects",
        "objects_per_min",
    ] + [f"counter_{i}" for i in range(1, 13)]

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i in range(3):
            row = {
                "timestamp": f"2025-01-01T00:00:0{i}",
                "capacity": float(10 * (i + 1)),
                "accepts": float(8 * (i + 1)),
                "rejects": float(2 * (i + 1)),
                "objects_per_min": 60,
            }
            for j in range(1, 13):
                row[f"counter_{j}"] = i + 1 if j == 1 else 0
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
    assert list(graph.figure.data[0].y) == [60.0, 60.0, 60.0]


def test_update_section_1_1_lab_uses_log(monkeypatch, tmp_path):
    app = setup_app(monkeypatch, tmp_path)
    csv_path = create_lab_metrics(tmp_path)
    func = app.callback_map["section-1-1.children"]["callback"]

    callbacks.previous_counter_values = [0] * 12

    content, prod = func.__wrapped__(
        0,
        "main",
        {},
        {},
        "en",
        {"connected": False},
        {"mode": "lab"},
        {},
        {"unit": "lb"},
    )

    metrics = callbacks.load_lab_totals_metrics(1)
    total_lbs, acc_lbs, rej_lbs, _ = metrics
    expected = {
        "capacity": callbacks.convert_capacity_from_lbs(total_lbs, {"unit": "lb"}),
        "accepts": callbacks.convert_capacity_from_lbs(acc_lbs, {"unit": "lb"}),
        "rejects": callbacks.convert_capacity_from_lbs(rej_lbs, {"unit": "lb"}),
    }

    counter_totals, _, object_totals = callbacks.load_lab_totals(1)
    reject_count = sum(counter_totals)
    capacity_count = object_totals[-1]
    accepts_count = max(0, capacity_count - reject_count)

    unit_label = callbacks.capacity_unit_label({"unit": "lb"})
    unit_label_plain = callbacks.capacity_unit_label({"unit": "lb"}, False)

    assert prod == expected

    cap_text = content.children[1].children[2].children
    acc_text = content.children[2].children[2].children
    rej_text = content.children[3].children[2].children

    assert cap_text == f"{capacity_count:,.0f} pcs / {expected['capacity']:,.0f} {unit_label}"
    assert acc_text == f"{accepts_count:,.0f} pcs / {expected['accepts']:,.0f} {unit_label_plain} "
    assert rej_text == f"{reject_count:,.0f} obj / {expected['rejects']:,.0f} {unit_label_plain} "
