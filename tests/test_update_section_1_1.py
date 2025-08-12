import os
import sys
import dash

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks
import autoconnect
import generate_report


def test_update_section_1_1_lab_running_counts(monkeypatch, tmp_path):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    key = next(k for k in app.callback_map if 'section-1-1' in k)
    func = app.callback_map[key]["callback"]

    callbacks.hourly_data_saving.EXPORT_DIR = str(tmp_path)
    machine_dir = tmp_path / "1"
    machine_dir.mkdir()
    (machine_dir / "Lab_Test_sample.csv").write_text(
        "timestamp,objects_60M,counter_1\n"
        "2025-01-01T00:00:00,10,2\n"
        "2025-01-01T00:01:00,10,2\n"
    )

    class Dummy:
        def __init__(self, val):
            self.latest_value = val

    callbacks.app_state = type("S", (), {})()
    callbacks.app_state.tags = {
        "Settings.ColorSort.TestWeightValue": {"data": Dummy(1)},
        "Settings.ColorSort.TestWeightCount": {"data": Dummy(10)},
        "Settings.ColorSort.TestWeightUnit": {"data": Dummy("lb")},
    }

    callbacks._lab_running_state = True
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
        {"unit": "kg"},
        {"machines": []},
    )

    accept_row = section.children[2]
    reject_row = section.children[3]
    assert accept_row.children[-1].children == "(80.00%)"
    assert reject_row.children[-1].children == "(20.00%)"


def test_update_section_1_1_lab_stopped_counts(monkeypatch, tmp_path):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    key = next(k for k in app.callback_map if 'section-1-1' in k)
    func = app.callback_map[key]["callback"]

    callbacks.hourly_data_saving.EXPORT_DIR = str(tmp_path)
    machine_dir = tmp_path / "1"
    machine_dir.mkdir()
    (machine_dir / "Lab_Test_sample.csv").write_text(
        "timestamp,objects_60M,counter_1\n"
        "2025-01-01T00:00:00,10,5\n"
        "2025-01-01T00:01:00,10,5\n"
    )

    class Dummy:
        def __init__(self, val):
            self.latest_value = val

    callbacks.app_state = type("S", (), {})()
    callbacks.app_state.tags = {
        "Settings.ColorSort.TestWeightValue": {"data": Dummy(1)},
        "Settings.ColorSort.TestWeightCount": {"data": Dummy(10)},
        "Settings.ColorSort.TestWeightUnit": {"data": Dummy("lb")},
    }

    callbacks._lab_running_state = False
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


def test_update_section_1_1_lab_weight_from_counts(monkeypatch, tmp_path):
    """Weights in lab mode should use counter totals and the test-weight multiplier."""

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
        "timestamp,objects_60M,counter_1\n"
        "2025-01-01T00:00:00,10,2\n"
        "2025-01-01T00:01:00,10,2\n"
    )

    callbacks._lab_production_cache.clear()
    callbacks._lab_totals_cache.clear()
    callbacks.active_machine_id = 1

    class Dummy:
        def __init__(self, val):
            self.latest_value = val

    callbacks.app_state = type("S", (), {})()
    callbacks.app_state.tags = {
        "Settings.ColorSort.TestWeightValue": {"data": Dummy(1)},
        "Settings.ColorSort.TestWeightCount": {"data": Dummy(10)},
        "Settings.ColorSort.TestWeightUnit": {"data": Dummy("lb")},
    }

    # Compute expected weights from counts and multiplier
    active_flags = [True] + [False] * 11
    counts, _, objects = callbacks.load_lab_totals(1, active_counters=active_flags)
    capacity_count = objects[-1]
    reject_count = counts[0]
    accepts_count = max(0, capacity_count - reject_count)
    lab_mult = generate_report.lab_weight_multiplier_from_settings(
        {
            "Settings.ColorSort.TestWeightValue": 1,
            "Settings.ColorSort.TestWeightCount": 10,
            "Settings.ColorSort.TestWeightUnit": "lb",
        }
    )
    expected_accepts = accepts_count * lab_mult
    expected_rejects = reject_count * lab_mult

    callbacks._lab_totals_cache.clear()

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
    assert f"{expected_accepts:.2f} lb" in accept_row.children[2].children
    assert f"{expected_rejects:.2f} lb" in reject_row.children[2].children
