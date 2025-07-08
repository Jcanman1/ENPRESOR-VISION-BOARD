import os
import csv
import dash

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
    fieldnames = [
        "timestamp",
        "capacity",
        "accepts",
        "rejects",
        "objects_per_min",
        "running",
        "stopped",
    ] + [f"counter_{i}" for i in range(1, 13)] + ["mode"]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        row = {
            "timestamp": "2025-01-01T00:00:00",
            "capacity": "100",
            "accepts": "80",
            "rejects": "20",
            "objects_per_min": "60",
            "running": "1",
            "stopped": "0",
            "mode": "Lab",
        }
        for i in range(1, 13):
            row[f"counter_{i}"] = "0"
        writer.writerow(row)
    return path


def test_update_section_1_1_lab_reads_log(monkeypatch, tmp_path):
    app = setup_app(monkeypatch, tmp_path)
    create_log(tmp_path)
    callbacks.active_machine_id = 1
    key = next(k for k in app.callback_map if k.startswith("..section-1-1.children"))
    func = app.callback_map[key]["callback"]

    _, prod = func.__wrapped__(0, "main", {}, {}, "en", {"connected": False}, {"mode": "lab"}, {}, {"unit": "lb"})

    with (tmp_path/"1"/"Lab_Test_sample.csv").open() as f:
        rows = list(csv.DictReader(f))

    timestamps = [r["timestamp"] for r in rows]
    cap_values = [float(r["capacity"]) for r in rows]
    stats = callbacks.generate_report.calculate_total_capacity_from_csv_rates(
        cap_values, timestamps=timestamps, is_lab_mode=True
    )
    cap_avg = stats["average_rate_lbs_per_hr"]
    acc_total = sum(float(r["accepts"]) for r in rows)
    counter_totals, _, _ = callbacks.load_lab_totals(1)
    rej_weight = callbacks.convert_capacity_from_kg(sum(counter_totals) * 46, {"unit": "lb"})

    assert prod["capacity"] == cap_avg
    assert prod["accepts"] == acc_total
    assert prod["rejects"] == rej_weight
