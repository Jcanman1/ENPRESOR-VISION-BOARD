import json
import importlib
from pathlib import Path

import report_tags

module_name = "EnpresorOPCDataViewBeforeRestructureLegacy"

class DummyNode:
    def __init__(self, value):
        self.value = value
    def get_value(self):
        return self.value


def test_save_machine_settings(tmp_path):
    mod = importlib.import_module(module_name)
    tags = {}
    value_map = {name: i for i, name in enumerate(report_tags.REPORT_SETTINGS_TAGS)}
    for name, val in value_map.items():
        tags[name] = {"node": DummyNode(val), "data": mod.TagData(name)}
    connections = {"1": {"client": object(), "tags": tags, "connected": True, "last_update": None}}

    report_tags.save_machine_settings("1", connections, export_dir=tmp_path)
    settings_file = Path(tmp_path) / "1" / "settings.json"
    assert settings_file.exists()
    data = json.loads(settings_file.read_text())
    for name, val in value_map.items():
        assert data[name] == val


def test_save_machine_settings_active_only(tmp_path):
    mod = importlib.import_module(module_name)
    tags = {
        "Settings.Ejectors.PrimaryDelay": {"node": DummyNode(1), "data": mod.TagData("d")},
        "Settings.ColorSort.Primary1.Sensitivity": {"node": DummyNode(10), "data": mod.TagData("s1")},
        "Settings.ColorSort.Primary1.IsAssigned": {"node": DummyNode(True), "data": mod.TagData("a1")},
        "Settings.ColorSort.Primary2.Sensitivity": {"node": DummyNode(20), "data": mod.TagData("s2")},
        "Settings.ColorSort.Primary2.IsAssigned": {"node": DummyNode(False), "data": mod.TagData("a2")},
    }
    connections = {
        "1": {"client": object(), "tags": tags, "connected": True, "last_update": None}
    }

    report_tags.save_machine_settings(
        "1", connections, export_dir=tmp_path, active_only=True
    )

    settings_file = Path(tmp_path) / "1" / "settings.json"
    data = json.loads(settings_file.read_text())

    assert data["Settings.Ejectors.PrimaryDelay"] == 1
    assert data["Settings.ColorSort.Primary1.Sensitivity"] == 10
    assert data["Settings.ColorSort.Primary1.IsAssigned"] is True
    assert data["Settings.ColorSort.Primary2.IsAssigned"] is False
    assert "Settings.ColorSort.Primary2.Sensitivity" not in data
