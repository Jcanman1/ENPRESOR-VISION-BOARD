import os
import sys
import dash

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks
import autoconnect
from datetime import datetime


def test_update_section_3_2_updates_timestamp(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    func = app.callback_map["section-3-2.children"]["callback"]

    callbacks.app_state.tags = {}
    callbacks.app_state.connected = True

    class FakeDT(datetime.__class__):
        @classmethod
        def now(cls, tz=None):
            return datetime(2025, 1, 1, 0, 0, 0)

    monkeypatch.setattr(callbacks, "datetime", FakeDT)
    res1 = func.__wrapped__(0, "main", "en", {"connected": True}, {"mode": "live"})
    ts1 = res1.children[1].children[1].children[3].children[1]

    class FakeDT2(datetime.__class__):
        @classmethod
        def now(cls, tz=None):
            return datetime(2025, 1, 1, 0, 0, 1)

    monkeypatch.setattr(callbacks, "datetime", FakeDT2)
    res2 = func.__wrapped__(1, "main", "en", {"connected": True}, {"mode": "live"})
    ts2 = res2.children[1].children[1].children[3].children[1]

    assert ts1 != ts2
