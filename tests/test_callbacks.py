import os
import sys
import dash

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks
import autoconnect


def test_floor_machine_callback_registered():
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    assert "floor-machine-container.children" in app.callback_map


def test_register_callbacks_starts_autoconnect(monkeypatch):
    started = []

    class DummyThread:
        def __init__(self, *args, **kwargs):
            self.started = False
            started.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(autoconnect, "Thread", DummyThread)

    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)

    assert any(t.started for t in started)
