import os
import sys
import pytest

dash = pytest.importorskip("dash")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks

import EnpresorOPCDataViewBeforeRestructureLegacy as legacy
import autoconnect


def test_update_section_7_2_logs_changes(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    func = app.callback_map["section-7-2.children"]["callback"]

    machine_id = 1
    tag_name = next(iter(callbacks.MONITORED_RATE_TAGS))

    callbacks.app_state.tags = {tag_name: {"data": callbacks.TagData(tag_name)}}
    callbacks.app_state.connected = True
    legacy.machine_control_log.clear()

    callbacks.prev_values[machine_id][tag_name] = 10
    callbacks.app_state.tags[tag_name]["data"].latest_value = 20

    func.__wrapped__(0, "main", {}, "en", {"connected": True}, {"mode": "live"}, {"machine_id": machine_id})
    assert len(legacy.machine_control_log) == 1
    assert legacy.machine_control_log[0]["icon"] == "⬆"

    callbacks.app_state.tags[tag_name]["data"].latest_value = 30
    func.__wrapped__(1, "main", {}, "en", {"connected": True}, {"mode": "live"}, {"machine_id": machine_id})
    assert len(legacy.machine_control_log) == 2
    # Most recent entry is first in the log
    assert legacy.machine_control_log[0]["icon"] == "⬆"
    assert legacy.machine_control_log[1]["icon"] == "⬆"


def test_update_section_7_2_logs_preset_change(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    func = app.callback_map["section-7-2.children"]["callback"]

    machine_id = 2
    callbacks.app_state.tags = {callbacks.PRESET_NAME_TAG: {"data": callbacks.TagData("preset")}}
    callbacks.app_state.connected = True
    legacy.machine_control_log.clear()

    callbacks.prev_preset_names[machine_id] = "Old"
    callbacks.app_state.tags[callbacks.PRESET_NAME_TAG]["data"].latest_value = "New"

    func.__wrapped__(0, "main", {}, "en", {"connected": True}, {"mode": "live"}, {"machine_id": machine_id})
    assert len(legacy.machine_control_log) == 1
    assert legacy.machine_control_log[0]["icon"] == "🔄"
