import os
import sys
import pytest

dash = pytest.importorskip("dash")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks



def test_set_counter_view_mode_updates_setting(monkeypatch):
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    func = app.callback_map["counter-view-mode.data"]["callback"]
    original_values = list(range(1, 13))
    callbacks.previous_counter_values = original_values.copy()
    callbacks.threshold_settings = {i: {} for i in range(1, 13)}
    callbacks.threshold_settings["counter_mode"] = "counts"
    result = func.__wrapped__("percent")
    assert callbacks.previous_counter_values == original_values
    assert callbacks.threshold_settings["counter_mode"] == "percent"
    assert result == "percent"
