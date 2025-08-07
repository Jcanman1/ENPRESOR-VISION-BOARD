import os
import sys
import dash

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks
import autoconnect


def test_update_section_1_2_filters_inactive(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    monkeypatch.setattr(callbacks, "get_active_counter_flags", lambda mid: [True, False] + [False]*10)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    key = next(k for k in app.callback_map if 'section-1-2' in k)
    func = app.callback_map[key]["callback"]

    callbacks.previous_counter_values = [10, 20] + [0]*10
    callbacks.active_machine_id = 1

    div = func.__wrapped__(
        {"capacity": 100, "accepts": 80, "rejects": 20},
        0,
        "main",
        {},
        {},
        "counts",
        [10, 20] + [0] * 10,
        {"connected": True},
        {"mode": "lab"}
    )

    # Second pie chart should only include Counter 1
    fig = div.children[0].children[1].children.figure
    labels = list(fig.data[0].labels)
    assert labels == ["Counter 1"]

