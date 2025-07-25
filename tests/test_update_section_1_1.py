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
    )

    accept_row = section.children[2]
    reject_row = section.children[3]
    assert accept_row.children[-1].children == "(50.00%)"
    assert reject_row.children[-1].children == "(50.00%)"
