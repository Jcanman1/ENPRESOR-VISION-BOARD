import os
import sys
import pytest

dash = pytest.importorskip("dash")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks
import autoconnect


CAPACITY_TAG = "Status.ColorSort.Sort1.Throughput.KgPerHour.Current"
OPM_TAG = "Status.ColorSort.Sort1.Throughput.ObjectPerMin.Current"
COUNTER_TAG = "Status.ColorSort.Sort1.DefectCount{}.Rate.Current"


def setup_app(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    return app


def test_update_section_1_1_rejects(monkeypatch):
    app = setup_app(monkeypatch)
    key = next(k for k in app.callback_map if k.startswith("..section-1-1.children"))
    func = app.callback_map[key]["callback"]

    callbacks.app_state.tags = {
        CAPACITY_TAG: {"data": callbacks.TagData(CAPACITY_TAG)},
        OPM_TAG: {"data": callbacks.TagData(OPM_TAG)},
    }
    callbacks.app_state.tags[CAPACITY_TAG]["data"].latest_value = 1000
    callbacks.app_state.tags[OPM_TAG]["data"].latest_value = 100
    callbacks.app_state.connected = True
    callbacks.previous_counter_values = [5, 5] + [0] * 10

    _, prod = func.__wrapped__(0, "main", {}, {}, "en", {"connected": True}, {"mode": "live"}, {}, {"unit": "lb"})

    expected_cap = 1000 * 2.205
    expected_rejects = expected_cap * (10 / 100)
    assert pytest.approx(prod["rejects"], rel=1e-6) == expected_rejects
    assert pytest.approx(prod["accepts"], rel=1e-6) == expected_cap - expected_rejects


def test_log_current_metrics_rejects(monkeypatch):
    app = setup_app(monkeypatch)
    func = app.callback_map["metric-logging-store.data"]["callback"]

    tags = {
        CAPACITY_TAG: {"data": callbacks.TagData(CAPACITY_TAG)},
        OPM_TAG: {"data": callbacks.TagData(OPM_TAG)},
    }
    for i in range(1, 13):
        tname = COUNTER_TAG.format(i)
        tags[tname] = {"data": callbacks.TagData(tname)}
        tags[tname]["data"].latest_value = 5

    tags[CAPACITY_TAG]["data"].latest_value = 1000
    tags[OPM_TAG]["data"].latest_value = 200

    callbacks.machine_connections = {1: {"tags": tags, "connected": True}}

    captured = {}

    def fake_append(metrics, machine_id=None, filename=None, mode=None):
        captured.update(metrics)

    monkeypatch.setattr(callbacks, "append_metrics", fake_append)

    func.__wrapped__(0, {"connected": True}, {"mode": "live"}, None, None, {"unit": "lb"}, False, None, None)

    expected_cap = 1000 * 2.205
    expected_rejects = expected_cap * (60 / 200)
    assert pytest.approx(captured["rejects"], rel=1e-6) == expected_rejects
    assert pytest.approx(captured["accepts"], rel=1e-6) == expected_cap - expected_rejects
