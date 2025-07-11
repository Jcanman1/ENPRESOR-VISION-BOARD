
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


def test_load_machine_settings(tmp_path):
    machine_dir = tmp_path / "1"
    machine_dir.mkdir()
    settings_file = machine_dir / "settings.json"
    json.dump({"value": 1}, open(settings_file, "w"))

    data = generate_report.load_machine_settings(tmp_path, "1")
    assert data == {"value": 1}

