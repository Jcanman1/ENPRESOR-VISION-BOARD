import os
import os
import sys
import csv
import pytest

import dash

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks
import autoconnect


def setup_app(monkeypatch, tmp_path):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    monkeypatch.setattr(callbacks.hourly_data_saving, "EXPORT_DIR", str(tmp_path))
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    return app


def create_metrics(tmp_path):
    machine_dir = tmp_path / "1"
    machine_dir.mkdir(parents=True, exist_ok=True)
    path = machine_dir / "last_24h_metrics.csv"
    fieldnames = ["timestamp"] + [f"counter_{i}" for i in range(1, 13)]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({"timestamp": "2025-01-01T00:00:00", "counter_1": 1})
        writer.writerow({"timestamp": "2025-01-01T00:01:00", "counter_1": 2})
    return path


def test_update_section_5_2_live_uses_csv_totals(monkeypatch, tmp_path):
    app = setup_app(monkeypatch, tmp_path)
    create_metrics(tmp_path)
    func = app.callback_map["section-5-2.children"]["callback"]

    callbacks.previous_counter_values = [0] * 12
    callbacks.threshold_settings = {}

    res = func.__wrapped__(0, "main", {}, {}, "en", {"connected": True}, {"mode": "live"}, {"machine_id": 1})

    assert callbacks.previous_counter_values[0] == pytest.approx(3)
    bar = res.children[1]
    assert bar.figure.data[0].y[0] == pytest.approx(3)
