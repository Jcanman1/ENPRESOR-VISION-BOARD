import os
import sys
import pytest

dash = pytest.importorskip("dash")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks


def _get_auto_set_func(app):
    for meta in app.callback_map.values():
        cb = meta.get("callback")
        if cb and getattr(cb, "__name__", "") == "auto_set_thresholds":
            return cb
    raise AssertionError("auto_set_thresholds callback not found")


def test_auto_set_thresholds_respects_mode(monkeypatch):
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    func = _get_auto_set_func(app)

    callbacks.previous_counter_values = [10] * 12
    callbacks.threshold_settings = {i: {} for i in range(1, 13)}

    mins, maxs = func.__wrapped__(1, 20, "counts")
    assert mins[0] == 8.0
    assert maxs[0] == 12.0
    assert callbacks.threshold_settings[1]["min_value"] == 8.0
    assert callbacks.threshold_settings[1]["max_value"] == 12.0

    callbacks.previous_counter_values = [10] * 12
    callbacks.threshold_settings = {i: {} for i in range(1, 13)}

    mins_p, maxs_p = func.__wrapped__(1, 20, "percent")
    val = 100 / 12
    assert mins_p[0] == pytest.approx(round(val * 0.8, 2))
    assert maxs_p[0] == pytest.approx(round(val * 1.2, 2))
    assert callbacks.threshold_settings[1]["min_value"] == pytest.approx(round(val * 0.8, 2))
    assert callbacks.threshold_settings[1]["max_value"] == pytest.approx(round(val * 1.2, 2))
