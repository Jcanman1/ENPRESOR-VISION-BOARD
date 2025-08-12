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


def test_image_error_components_exist(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    module_name = "EnpresorOPCDataViewBeforeRestructureLegacy"
    if module_name in sys.modules:
        mod = importlib.reload(sys.modules[module_name])
    else:
        mod = importlib.import_module(module_name)
    ids = [getattr(c, "id", None) for c in mod.app.layout.children]
    assert "image-error-store" in ids
    modal = next(c for c in mod.app.layout.children if getattr(c, "id", None) == "upload-modal")
    body_ids = [getattr(ch, "id", None) for ch in modal.children[1].children]
    assert "image-error-alert" in body_ids


def test_lab_test_stop_time_store_exists(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    module_name = "EnpresorOPCDataViewBeforeRestructureLegacy"
    if module_name in sys.modules:
        mod = importlib.reload(sys.modules[module_name])
    else:
        mod = importlib.import_module(module_name)
    store_ids = [getattr(c, "id", None) for c in mod.app.layout.children]
    assert "lab-test-stop-time" in store_ids
