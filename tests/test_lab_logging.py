import os
import sys
import pytest

import dash

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks
import autoconnect

CAPACITY_TAG = "Status.ColorSort.Sort1.Throughput.KgPerHour.Current"
OPM_TAG = "Status.ColorSort.Sort1.Throughput.ObjectPerMin.Current"


def setup_app(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    return app


def test_lab_logging_uses_single_file(monkeypatch):
    app = setup_app(monkeypatch)
    log_func = app.callback_map["metric-logging-store.data"]["callback"]
    start_func = app.callback_map["lab-test-info.data"]["callback"]

    tags = {
        CAPACITY_TAG: {"data": callbacks.TagData(CAPACITY_TAG)},
        OPM_TAG: {"data": callbacks.TagData(OPM_TAG)},
    }
    tags[CAPACITY_TAG]["data"].latest_value = 1000
    tags[OPM_TAG]["data"].latest_value = 100

    callbacks.machine_connections = {1: {"tags": tags, "connected": True}}

    captured = []

    def fake_append(metrics, machine_id=None, filename=None, mode=None):
        captured.append(filename)

    monkeypatch.setattr(callbacks, "append_metrics", fake_append)

    class DummyCtx:
        def __init__(self, prop_id):
            self.triggered = [{"prop_id": prop_id}]

    monkeypatch.setattr(callbacks, "callback_context", DummyCtx("start-test-btn"))
    info = start_func.__wrapped__(1, None, "MyTest")
    assert "filename" in info

    log_func.__wrapped__(0, {"connected": True}, {"mode": "lab"}, None, None, {"unit": "lb"}, True, {"machine_id": 1}, info)
    log_func.__wrapped__(1, {"connected": True}, {"mode": "lab"}, None, None, {"unit": "lb"}, True, {"machine_id": 1}, {})

    assert len(set(captured)) == 1


def test_lab_stop_retains_filename(monkeypatch):
    app = setup_app(monkeypatch)
    log_func = app.callback_map["metric-logging-store.data"]["callback"]
    info_func = app.callback_map["lab-test-info.data"]["callback"]

    tags = {
        CAPACITY_TAG: {"data": callbacks.TagData(CAPACITY_TAG)},
        OPM_TAG: {"data": callbacks.TagData(OPM_TAG)},
    }
    tags[CAPACITY_TAG]["data"].latest_value = 1000
    tags[OPM_TAG]["data"].latest_value = 100

    callbacks.machine_connections = {1: {"tags": tags, "connected": True}}

    captured = []

    monkeypatch.setattr(
        callbacks,
        "append_metrics",
        lambda metrics, machine_id=None, filename=None, mode=None: captured.append(filename),
    )

    class DummyCtx:
        def __init__(self, prop_id):
            self.triggered = [{"prop_id": prop_id}]

    monkeypatch.setattr(callbacks, "callback_context", DummyCtx("start-test-btn"))
    start_info = info_func.__wrapped__(1, None, "MyStopTest")
    assert "filename" in start_info

    # simulate pressing stop
    monkeypatch.setattr(callbacks, "callback_context", DummyCtx("stop-test-btn"))
    stop_info = info_func.__wrapped__(None, 1, "")
    assert stop_info == {}

    # log metrics while the lab test is still considered running
    log_func.__wrapped__(0, {"connected": True}, {"mode": "lab"}, None, None, {"unit": "lb"}, True, {"machine_id": 1}, stop_info)

    assert captured[-1] == start_info["filename"]


def test_lab_logging_clamps_negative(monkeypatch):
    app = setup_app(monkeypatch)
    log_func = app.callback_map["metric-logging-store.data"]["callback"]

    tags = {
        CAPACITY_TAG: {"data": callbacks.TagData(CAPACITY_TAG)},
        OPM_TAG: {"data": callbacks.TagData(OPM_TAG)},
    }
    tags[CAPACITY_TAG]["data"].latest_value = -100
    tags[OPM_TAG]["data"].latest_value = -50

    for i in range(1, 13):
        tname = f"Status.ColorSort.Sort1.DefectCount{i}.Rate.Current"
        tags[tname] = {"data": callbacks.TagData(tname)}
        tags[tname]["data"].latest_value = -1

    callbacks.machine_connections = {1: {"tags": tags, "connected": True}}

    captured = {}

    def fake_append(metrics, machine_id=None, filename=None, mode=None):
        captured.update(metrics)

    monkeypatch.setattr(callbacks, "append_metrics", fake_append)

    log_func.__wrapped__(0, {"connected": True}, {"mode": "lab"}, None, None, {"unit": "lb"}, True, {"machine_id": 1}, {"filename": "test.csv"})

    assert all(v >= 0 for v in captured.values() if isinstance(v, (int, float)))
