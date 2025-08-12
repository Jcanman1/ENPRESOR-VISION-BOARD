import os
import sys
import dash

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks
import autoconnect


def test_update_section_1_1_lab_running_counts(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    key = next(k for k in app.callback_map if 'section-1-1' in k)
    func = app.callback_map[key]["callback"]

    callbacks._lab_running_state = True
    callbacks.active_machine_id = 1
    callbacks._lab_production_cache[1] = {
        "mtime": 0,
        "size": 0,
        "production_data": {"capacity": 100, "accepts": 80, "rejects": 20},
        "capacity_count": 10,
        "accepts_count": 8,
        "reject_count": 2,
    }

    section, _ = func.__wrapped__(
        0,
        "main",
        {},
        {},
        "en",
        {"connected": True},
        {"mode": "lab"},
        {"capacity": 0, "accepts": 0, "rejects": 0},
        {"unit": "kg"},
        {"machines": []},
    )

    accept_row = section.children[2]
    reject_row = section.children[3]
    assert accept_row.children[-1].children == "(80.00%)"
    assert reject_row.children[-1].children == "(20.00%)"


def test_update_section_1_1_lab_stopped_counts(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    key = next(k for k in app.callback_map if 'section-1-1' in k)
    func = app.callback_map[key]["callback"]

    callbacks._lab_running_state = False
    callbacks.active_machine_id = 1
    callbacks._lab_production_cache[1] = {
        "mtime": 0,
        "size": 0,
        "production_data": {"capacity": 100, "accepts": 80, "rejects": 20},
        "capacity_count": 10,
        "accepts_count": 5,
        "reject_count": 5,
    }

    section, _ = func.__wrapped__(
        0,
        "main",
        {},
        {},
        "en",
        {"connected": True},
        {"mode": "lab"},
        {"capacity": 0, "accepts": 0, "rejects": 0},
        {"unit": "kg"},
        {"machines": []},
    )

    accept_row = section.children[2]
    reject_row = section.children[3]
    assert accept_row.children[-1].children == "(50.00%)"
    assert reject_row.children[-1].children == "(50.00%)"


def test_update_section_1_1_demo_matches_machine(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    key = next(k for k in app.callback_map if 'section-1-1' in k)
    func = app.callback_map[key]["callback"]

    callbacks.active_machine_id = 1
    machines_data = {
        "machines": [
            {
                "id": 1,
                "operational_data": {
                    "production": {
                        "capacity": 100.0,
                        "accepts": 94.0,
                        "rejects": 6.0,
                    }
                },
            }
        ]
    }

    section, prod = func.__wrapped__(
        0,
        "main",
        {},
        {},
        "en",
        {"connected": True},
        {"mode": "demo"},
        {"capacity": 0, "accepts": 0, "rejects": 0},
        {"unit": "kg"},
        machines_data,
    )

    reject_row = section.children[3]
    reject_display = reject_row.children[2].children
    assert "pcs" not in reject_display
    assert "6.00" in reject_display
    assert prod == {"capacity": 100.0, "accepts": 94.0, "rejects": 6.0}


def test_update_section_1_1_lab_weight_from_metrics(monkeypatch, tmp_path):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    key = next(k for k in app.callback_map if 'section-1-1' in k)
    func = app.callback_map[key]["callback"]

    callbacks.hourly_data_saving.EXPORT_DIR = str(tmp_path)
    machine_dir = tmp_path / "1"
    machine_dir.mkdir()

    csv_path = machine_dir / "Lab_Test_sample.csv"
    csv_path.write_text(
        "timestamp,accepts,rejects,objects_60M,counter_1\n"
        "2025-01-01T00:00:00,54,6,1800,180\n"
        "2025-01-01T00:01:00,54,6,1800,180\n"
    )

    callbacks._lab_production_cache.clear()
    callbacks._lab_totals_cache.clear()
    callbacks.active_machine_id = 1

    section, _ = func.__wrapped__(
        0,
        "main",
        {},
        {},
        "en",
        {"connected": True},
        {"mode": "lab"},
        {"capacity": 0, "accepts": 0, "rejects": 0},
        {"unit": "lb"},
        {"machines": []},
    )

    accept_row = section.children[2]
    reject_row = section.children[3]
    assert "0.90 lb" in accept_row.children[2].children
    assert "0.10 lb" in reject_row.children[2].children
