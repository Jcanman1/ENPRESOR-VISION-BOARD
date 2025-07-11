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
