
import json
from pathlib import Path

import generate_report


def test_lookup_setting_nested():
    data = {
        "Settings": {
            "Ejectors": {"PrimaryDelay": 10},
            "Calibration": {"FrontProductRed": 5},
        }
    }
    assert generate_report._lookup_setting(data, "Settings.Ejectors.PrimaryDelay") == 10
    assert (
        generate_report._lookup_setting(data, "Settings.Calibration.FrontProductRed")
        == 5
    )
    assert generate_report._lookup_setting(data, "Missing.Key") == "N/A"


def test_lookup_setting_flat_keys():
    flat = {"Settings.Ejectors.PrimaryDelay": 10}
    assert generate_report._lookup_setting(flat, "Settings.Ejectors.PrimaryDelay") == 10
    assert generate_report._lookup_setting(flat, "Missing.Key") == "N/A"


def test_load_machine_settings(tmp_path):
    machine_dir = tmp_path / "1"
    machine_dir.mkdir()
    settings_file = machine_dir / "settings.json"
    json.dump({"value": 1}, open(settings_file, "w"))

    data = generate_report.load_machine_settings(tmp_path, "1")
    assert data == {"value": 1}


def test_bool_from_setting_case_insensitive():
    assert generate_report._bool_from_setting("TRUE") is True
    assert generate_report._bool_from_setting("FALSE") is False


def test_draw_sensitivity_sections_only_active(monkeypatch):
    calls = []

    def fake_grid(c, x0, y0, w, h, settings, primary_num, *, lang="en", **kwargs):
        calls.append(primary_num)

    monkeypatch.setattr(generate_report, "draw_sensitivity_grid", fake_grid)

    settings = {
        "Settings": {
            "ColorSort": {
                "Primary1": {"IsAssigned": "TRUE"},
                "Primary2": {"IsAssigned": "FALSE"},
                "Primary3": {"IsAssigned": "TRUE"},
            }
        }
    }

    end_y = generate_report.draw_sensitivity_sections(
        None, 0, 100, 50, 10, settings
    )

    assert calls == [1, 3]
    assert end_y == 100 - 2 * (10 + 10)




def test_primary7_typeid_label_lab_mode():

    class DummyCanvas:
        def __init__(self):
            self.texts = []

        def saveState(self):
            pass

        def restoreState(self):
            pass

        def setStrokeColor(self, *a, **k):
            pass

        def line(self, *a, **k):
            pass

        def rect(self, *a, **k):
            pass

        def setFillColor(self, *a, **k):
            pass

        def setFont(self, *a, **k):
            pass

        def drawString(self, x, y, text):
            self.texts.append(text)


    for value, expected in [(0, "Ellipsoid"), (1, "Grid")]:

        c = DummyCanvas()
        settings = {"Settings": {"ColorSort": {"Primary7": {"TypeId": value}}}}
        generate_report.draw_sensitivity_grid(
            c, 0, 0, 100, 20, settings, 7, is_lab_mode=True
        )
        assert expected in c.texts


def test_position_text_from_axis_wave_lab_mode():
    class DummyCanvas:
        def __init__(self):
            self.texts = []

        def saveState(self):
            pass

        def restoreState(self):
            pass

        def setStrokeColor(self, *a, **k):
            pass

        def line(self, *a, **k):
            pass

        def rect(self, *a, **k):
            pass

        def setFillColor(self, *a, **k):
            pass

        def setFont(self, *a, **k):
            pass

        def drawString(self, x, y, text):
            self.texts.append(text)

    cases = [
        ({"XAxisWave": "9", "YAxisWave": "7", "ZAxisWave": "8"}, "Top Right"),
        ({"XAxisWave": "8", "YAxisWave": "7", "ZAxisWave": "9"}, "Top Left"),
        ({"XAxisWave": "8", "YAxisWave": "9", "ZAxisWave": "7"}, "Bottom"),
    ]

    for waves, expected in cases:
        c = DummyCanvas()
        settings = {"Settings": {"ColorSort": {"Primary1": {"TypeId": 1, **waves}}}}
        generate_report.draw_sensitivity_grid(c, 0, 0, 100, 20, settings, 1, is_lab_mode=True)
        assert expected in c.texts



