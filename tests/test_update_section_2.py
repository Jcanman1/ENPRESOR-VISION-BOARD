import os
import sys
import dash

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks
import autoconnect


def test_update_section_2_handles_no_state_in_live_mode(monkeypatch):
    """Section 2 should render even if app_state data is missing."""
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    func = app.callback_map["section-2.children"]["callback"]

    res = func.__wrapped__(0, "main", "en", None, {"mode": "live"})
    status_text = res.children[2].children[0].children[0].children[1].children

    assert status_text == "Unknown"


def test_update_section_2_demo_mode_without_connection(monkeypatch):
    """Demo mode should display demo status when no machine is connected."""
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    func = app.callback_map["section-2.children"]["callback"]

    res = func.__wrapped__(0, "main", "en", None, {"mode": "demo"})
    status_text = res.children[2].children[0].children[0].children[1].children

    assert status_text == "GOOD"
