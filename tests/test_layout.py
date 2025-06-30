import os
import sys
import importlib
import pytest

dash = pytest.importorskip("dash")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import autoconnect


def test_dashboard_nav_safety_store_exists(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    module_name = "EnpresorOPCDataViewBeforeRestructureLegacy"
    if module_name in sys.modules:
        mod = importlib.reload(sys.modules[module_name])
    else:
        mod = importlib.import_module(module_name)
    store_ids = [getattr(c, "id", None) for c in mod.app.layout.children]
    assert "dashboard-nav-safety" in store_ids
