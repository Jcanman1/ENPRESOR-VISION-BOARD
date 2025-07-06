import os
import sys
import pytest

dash = pytest.importorskip("dash")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import callbacks
import autoconnect


def test_floor_machine_callback_registered():
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    assert "floor-machine-container.children" in app.callback_map


def test_register_callbacks_starts_autoconnect(monkeypatch):
    started = []

    class DummyThread:
        def __init__(self, *args, **kwargs):
            self.started = False
            started.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(autoconnect, "Thread", DummyThread)

    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)

    assert any(t.started for t in started)


def test_register_callbacks_uses_existing_module(monkeypatch):
    """Existing module instance should be reused without reimporting."""
    from types import ModuleType

    existing = ModuleType("EnpresorOPCDataViewBeforeRestructureLegacy")
    existing.sentinel = 123

    monkeypatch.setitem(sys.modules,
                        "EnpresorOPCDataViewBeforeRestructureLegacy", existing)

    called = False
    orig_import = callbacks.importlib.import_module

    def fake_import(name, package=None):
        nonlocal called
        if name == "EnpresorOPCDataViewBeforeRestructureLegacy":
            called = True
            return existing
        return orig_import(name, package)

    monkeypatch.setattr(callbacks.importlib, "import_module", fake_import)
    def stop():
        raise RuntimeError("stop")

    monkeypatch.setattr(autoconnect, "initialize_autoconnect", stop)

    app = dash.Dash(__name__)
    with pytest.raises(RuntimeError):
        callbacks.register_callbacks(app)

    assert not called
    assert callbacks.sentinel == 123


def test_register_callbacks_uses_main_module(monkeypatch):
    """When executed as a script, __main__ should supply globals."""
    from types import ModuleType

    main_mod = ModuleType("__main__")
    main_mod.__file__ = "EnpresorOPCDataViewBeforeRestructureLegacy.py"
    main_mod.sentinel = 1

    monkeypatch.setitem(sys.modules, "__main__", main_mod)
    monkeypatch.delitem(sys.modules, "EnpresorOPCDataViewBeforeRestructureLegacy", raising=False)

    called = False
    orig_import = callbacks.importlib.import_module

    def fake_import(name, package=None):
        nonlocal called
        if name == "EnpresorOPCDataViewBeforeRestructureLegacy":
            called = True
            return main_mod
        return orig_import(name, package)

    monkeypatch.setattr(callbacks.importlib, "import_module", fake_import)
    def stop():
        raise RuntimeError("stop")

    monkeypatch.setattr(autoconnect, "initialize_autoconnect", stop)

    app = dash.Dash(__name__)
    with pytest.raises(RuntimeError):
        callbacks.register_callbacks(app)

    # modify state and call again
    main_mod.sentinel = 2
    with pytest.raises(RuntimeError):
        callbacks.register_callbacks(app)

    assert not called
    assert callbacks.sentinel == 2


def test_register_callbacks_no_recursion(monkeypatch):
    """Importing legacy module should not re-run initialization."""
    init_calls = []

    def dummy_init():
        init_calls.append(1)

    monkeypatch.setattr(autoconnect, "initialize_autoconnect", dummy_init)
    monkeypatch.delitem(sys.modules, "EnpresorOPCDataViewBeforeRestructureLegacy", raising=False)

    callbacks._REGISTERING = False

    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)

    assert init_calls == [1]


def test_lab_buttons_callback(monkeypatch):
    """Start/stop button callback should be registered and return proper state."""
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)

    key = next(k for k in app.callback_map if "start-test-btn.disabled" in k)
    func = app.callback_map[key]["callback"]

    # Not running yet
    res = func.__wrapped__(False, "lab")
    assert res == (False, "success", True, "secondary")

    # Running
    res = func.__wrapped__(True, "lab")
    assert res == (True, "secondary", False, "danger")

    # Other mode
    res = func.__wrapped__(False, "live")
    assert res == (True, "secondary", True, "secondary")


def test_refresh_text_includes_lab_controls(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)

    key = next(k for k in app.callback_map if "threshold-modal-header.children" in k)
    outputs = [out.component_id + "." + out.component_property for out in app.callback_map[key]["output"]]

    assert "start-test-btn.children" in outputs
    assert "lab-test-name.placeholder" in outputs
    assert "display-tab.label" in outputs
