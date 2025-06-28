import os
import sys
import dash

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks


def test_floor_machine_callback_registered():
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    assert "floor-machine-container.children" in app.callback_map
