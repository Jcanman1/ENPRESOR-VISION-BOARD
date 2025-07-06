import os
import sys
import importlib
import pytest

dash = pytest.importorskip("dash")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import autoconnect


def _get_label_text(row):
    col = row.children[0]
    div = col.children
    return getattr(div, "children", div)


def test_threshold_form_translations(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    module_name = "EnpresorOPCDataViewBeforeRestructureLegacy"
    mod = importlib.import_module(module_name)
    rows_en = mod.create_threshold_settings_form("en")
    rows_es = mod.create_threshold_settings_form("es")
    assert _get_label_text(rows_en[0]) == "Sensitivity 1:"
    assert "Sensibilidad" in _get_label_text(rows_es[0])


def test_display_form_translations(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    module_name = "EnpresorOPCDataViewBeforeRestructureLegacy"
    mod = importlib.import_module(module_name)
    form_es = mod.create_display_settings_form("es")
    first_row = form_es.children[1]
    assert "Sensibilidad" in _get_label_text(first_row)
