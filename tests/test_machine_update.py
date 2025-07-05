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


def test_get_machine_operational_data_uses_counters():
    mod = importlib.import_module(module_name)

    CAPACITY_TAG = "Status.ColorSort.Sort1.Throughput.KgPerHour.Current"
    OPM_TAG = "Status.ColorSort.Sort1.Throughput.ObjectPerMin.Current"
    COUNTER_TAG = "Status.ColorSort.Sort1.DefectCount{}.Rate.Current"

    mod.machine_connections.clear()

    tags = {
        CAPACITY_TAG: {"node": DummyNode(1000), "data": mod.TagData(CAPACITY_TAG)},
        OPM_TAG: {"node": DummyNode(100), "data": mod.TagData(OPM_TAG)},
    }

    for i in range(1, 13):
        val = 5 if i <= 2 else 0
        name = COUNTER_TAG.format(i)
        tags[name] = {"node": DummyNode(val), "data": mod.TagData(name)}

    mod.machine_connections[1] = {
        "client": object(),
        "tags": tags,
        "connected": True,
        "last_update": None,
    }

    mod.update_machine_connections()

    data = mod.get_machine_operational_data(1)
    prod = data["production"]

    # Capacity is converted to lbs by default (1000 kg -> 2205 lbs)
    expected_rejects = 2205 * (10 / 100)
    assert prod["rejects_formatted"] == f"{expected_rejects:,.0f}"

