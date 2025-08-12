import os
import sys
import pytest

# Skip if dash is not installed
dash = pytest.importorskip("dash")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks


def test_update_alarms_uses_display_mode(monkeypatch):
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    func = app.callback_map["alarm-data.data"]["callback"]

    callbacks.previous_counter_values = [10] * 12
    callbacks.threshold_violation_state = {
        i: {"is_violating": False, "violation_start_time": None, "email_sent": False}
        for i in range(1, 13)
    }
    callbacks.threshold_settings = {
        i: {"min_enabled": True, "max_enabled": True, "min_value": 8.1, "max_value": 8.5}
        for i in range(1, 13)
    }
    callbacks.threshold_settings["counter_mode"] = "percent"
    callbacks.threshold_settings["email_enabled"] = False

    result = func.__wrapped__(0, {})

    assert result["alarms"] == []

