import os
import sys
import pytest

dash = pytest.importorskip("dash")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks
import autoconnect


def test_update_section_6_1_calls_memory_monitor(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    called = []
    monkeypatch.setattr(callbacks.mem_utils, "log_memory_if_high", lambda: called.append(True))

    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    func = app.callback_map["section-6-1.children"]["callback"]

    monkeypatch.setattr(callbacks.counter_manager, "add_data_point", lambda *a, **k: None, raising=False)

    callbacks.previous_counter_values = [0] * 12
    callbacks.display_settings = {i: True for i in range(1, 13)}
    callbacks.app_state.counter_history = {i: {"times": [], "values": []} for i in range(1, 13)}

    func.__wrapped__(0, "main", {}, "en", {"connected": True}, {"mode": "demo"}, {"machine_id": 1})

    assert called


def test_update_section_6_1_yaxis_min_zero(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)

    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    func = app.callback_map["section-6-1.children"]["callback"]

    monkeypatch.setattr(callbacks.counter_manager, "add_data_point", lambda *a, **k: None, raising=False)

    callbacks.previous_counter_values = list(range(12))
    callbacks.display_settings = {i: True for i in range(1, 13)}
    callbacks.app_state.counter_history = {i: {"times": [], "values": []} for i in range(1, 13)}

    div = func.__wrapped__(0, "main", {}, "en", {"connected": True}, {"mode": "demo"}, {"machine_id": 1})

    graph = div.children[1]
    assert graph.figure.layout.yaxis.range[0] == 0
