import pytest

dash = pytest.importorskip("dash")
from dash.exceptions import PreventUpdate

import callbacks


def _get_callback(app, output):
    return app.callback_map[output]["callback"].__wrapped__


def test_update_section_5_1_validation():
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    func = _get_callback(app, "section-5-1.children")

    with pytest.raises(PreventUpdate):
        func(0, "other", {}, {}, "en", {"connected": True}, {"mode": "live"}, {"machine_id": 1}, {}, "objects")

    with pytest.raises(PreventUpdate):
        func(0, "main", {}, {}, "en", {"connected": True}, {"mode": "live"}, None, {}, "objects")

    with pytest.raises(PreventUpdate):
        func(0, "main", {}, {}, "en", {"connected": False}, {"mode": "live"}, {"machine_id": 1}, {}, "objects")
