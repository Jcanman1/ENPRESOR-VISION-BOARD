import importlib
import pytest

module_name = "EnpresorOPCDataViewBeforeRestructureLegacy"

class DummyNode:
    def __init__(self, value):
        self.value = value
    def get_value(self):
        return self.value

def test_update_machine_connections_updates_tags_and_dashboard():
    mod = importlib.import_module(module_name)

    mod.machine_connections.clear()
    tag = mod.TagData("Status.Info.Serial")
    mod.machine_connections[1] = {
        "client": object(),
        "tags": {"Status.Info.Serial": {"node": DummyNode("SN"), "data": tag}},
        "connected": True,
        "last_update": None,
    }
    mod.update_machine_connections()
    assert tag.latest_value == "SN"
    data = mod.get_machine_current_data(1)
    assert data["serial"] == "SN"

