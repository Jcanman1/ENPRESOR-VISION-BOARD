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

    callbacks._lab_running_state = False
    callbacks._grace_start_time = None
    res = func.__wrapped__(False, None, "lab")

    assert res == (False, "success", True, "secondary")

    # Running
    callbacks._lab_running_state = True
    callbacks._grace_start_time = None

    res = func.__wrapped__(True, None, "lab")

    assert res == (True, "secondary", False, "danger")

    # Grace period after stopping
    callbacks._lab_running_state = True
    callbacks._grace_start_time = 90.0
    monkeypatch.setattr(callbacks.time, "time", lambda: 100.0)

    res = func.__wrapped__(True, 90.0, "lab")

    assert res == (True, "secondary", True, "secondary")

    # Other mode
    callbacks._lab_running_state = False
    callbacks._grace_start_time = None

    res = func.__wrapped__(False, None, "live")

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


def test_memory_management_callback(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)

    # Find memory management callback
    key = next(k for k in app.callback_map if "memory-metrics-store.data" in k)
    func = app.callback_map[key]["callback"]

    # Populate history with more than max_points entries
    callbacks.app_state.counter_history = {
        i: {"times": list(range(150)), "values": list(range(150))} for i in range(1, 13)
    }

    result = func.__wrapped__(0)

    max_points = result["max_points"]
    assert all(len(callbacks.app_state.counter_history[i]["times"]) <= max_points for i in range(1, 13))
    assert "rss_mb" in result


def test_generate_report_disable_callback(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)

    key = next(k for k in app.callback_map if "generate-report-btn.disabled" in k)
    func = app.callback_map[key]["callback"]

    callbacks._lab_running_state = True

    callbacks._grace_start_time = 90

    monkeypatch.setattr(callbacks.time, "time", lambda: 100.0)
    assert func.__wrapped__(0, True, 90) is True

    callbacks._lab_running_state = False
    callbacks._grace_start_time = 95
    monkeypatch.setattr(callbacks.time, "time", lambda: 100.0)
    assert func.__wrapped__(0, False, 95) is True

    callbacks._lab_running_state = False
    callbacks._grace_start_time = 50
    monkeypatch.setattr(callbacks.time, "time", lambda: 100.0)
    assert func.__wrapped__(0, False, 50) is False


def test_lab_auto_start(monkeypatch):
    """Lab mode should start automatically when any feeder is running."""
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    callbacks._lab_running_state = False
    callbacks._grace_start_time = None

    func = app.callback_map["..lab-test-running.data...lab-test-stop-time.data.."]["callback"]

    tag = callbacks.TagData("Status.Feeders.1IsRunning")
    tag.latest_value = True
    callbacks.machine_connections = {
        1: {"tags": {"Status.Feeders.1IsRunning": {"data": tag}}, "connected": True}
    }
    callbacks.active_machine_id = 1

    class DummyCtx:
        def __init__(self, prop_id):
            self.triggered = [{"prop_id": prop_id}]

    monkeypatch.setattr(callbacks, "callback_context", DummyCtx("status-update-interval.n_intervals"))

    res = func.__wrapped__(None, None, "lab", 1, False, None, "AutoTest", {"mode": "lab"}, {"machine_id": 1}, "feeder")
    assert res[0] is False


def test_lab_local_mode_no_auto_start(monkeypatch):
    """Feeder activity should not start logging when using Local Start."""
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    callbacks._lab_running_state = False
    callbacks._grace_start_time = None

    func = app.callback_map["..lab-test-running.data...lab-test-stop-time.data.."]["callback"]

    tag = callbacks.TagData("Status.Feeders.1IsRunning")
    tag.latest_value = True
    callbacks.machine_connections = {
        1: {"tags": {"Status.Feeders.1IsRunning": {"data": tag}}, "connected": True}
    }
    callbacks.active_machine_id = 1

    class DummyCtx:
        def __init__(self, prop_id):
            self.triggered = [{"prop_id": prop_id}]

    monkeypatch.setattr(callbacks, "callback_context", DummyCtx("status-update-interval.n_intervals"))

    res = func.__wrapped__(None, None, "lab", 1, False, None, "AutoTest", {"mode": "lab"}, {"machine_id": 1}, "local")
    assert res[0] is False



def test_lab_auto_stop_sets_time(monkeypatch):
    """Stop time should be recorded when all feeders stop running."""
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    callbacks._lab_running_state = False
    callbacks._grace_start_time = None

    func = app.callback_map["..lab-test-running.data...lab-test-stop-time.data.."]["callback"]

    tag = callbacks.TagData("Status.Feeders.1IsRunning")
    tag.latest_value = False
    callbacks.machine_connections = {
        1: {"tags": {"Status.Feeders.1IsRunning": {"data": tag}}, "connected": True}
    }
    callbacks.active_machine_id = 1

    class DummyCtx:
        def __init__(self, prop_id):
            self.triggered = [{"prop_id": prop_id}]

    monkeypatch.setattr(callbacks, "callback_context", DummyCtx("status-update-interval.n_intervals"))
    monkeypatch.setattr(callbacks.time, "time", lambda: 123.0)
    res = func.__wrapped__(None, None, "lab", 1, True, None, "AutoTest", {"mode": "lab"}, {"machine_id": 1}, "feeder")
    assert res[1] is None


def test_lab_restart_clears_stop_time(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    callbacks._lab_running_state = False
    callbacks._grace_start_time = None

    func = app.callback_map["..lab-test-running.data...lab-test-stop-time.data.."]["callback"]

    tag = callbacks.TagData("Status.Feeders.1IsRunning")
    tag.latest_value = True
    callbacks.machine_connections = {
        1: {"tags": {"Status.Feeders.1IsRunning": {"data": tag}}, "connected": True}
    }
    callbacks.active_machine_id = 1

    class DummyCtx:
        def __init__(self, prop_id):
            self.triggered = [{"prop_id": prop_id}]

    monkeypatch.setattr(callbacks, "callback_context", DummyCtx("status-update-interval.n_intervals"))
    res = func.__wrapped__(None, None, "lab", 1, True, 100.0, "AutoTest", {"mode": "lab"}, {"machine_id": 1}, "feeder")
    assert res[1] is None


def test_manual_stop_sets_negative_time(monkeypatch):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    callbacks._lab_running_state = True
    callbacks._grace_start_time = None

    func = app.callback_map["..lab-test-running.data...lab-test-stop-time.data.."]["callback"]

    class DummyCtx:
        def __init__(self, prop_id):
            self.triggered = [{"prop_id": prop_id}]

    monkeypatch.setattr(callbacks, "callback_context", DummyCtx("stop-test-btn.n_clicks"))
    monkeypatch.setattr(callbacks.time, "time", lambda: 456.0)

    res = func.__wrapped__(None, 1, "lab", 0, True, None, "AutoTest", {"mode": "lab"}, {"machine_id": 1}, "feeder")
    assert res[1] == 456.0


def test_grace_period_failsafe(monkeypatch):
    """Buttons should reset if grace period elapsed even if state not updated."""
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)

    # Fetch callbacks
    key_btn = next(k for k in app.callback_map if "start-test-btn.disabled" in k)
    toggle_func = app.callback_map[key_btn]["callback"]

    key_report = next(k for k in app.callback_map if "generate-report-btn.disabled" in k)
    report_func = app.callback_map[key_report]["callback"]

    # Simulate globals indicating running with stale stop time 50s ago
    callbacks._lab_running_state = True
    callbacks._grace_start_time = 50.0
    monkeypatch.setattr(callbacks.time, "time", lambda: 100.0)

    # Toggle buttons should treat test as stopped

    assert toggle_func.__wrapped__(0, True, 50.0, "lab") == (

        False,
        "success",
        True,
        "secondary",
    )

    # Report button should be enabled
    assert report_func.__wrapped__(0, False, 50.0) is False

def test_update_lab_state_failsafe(monkeypatch):
    """update_lab_state should clear stale grace period even without interval."""
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)

    func = app.callback_map["..lab-test-running.data...lab-test-stop-time.data.."]["callback"]

    callbacks._lab_running_state = True
    callbacks._grace_start_time = 50.0

    class DummyCtx:
        def __init__(self, prop_id):
            self.triggered = [{"prop_id": prop_id}]

    monkeypatch.setattr(callbacks, "callback_context", DummyCtx("start-test-btn.n_clicks"))
    monkeypatch.setattr(callbacks.time, "time", lambda: 100.0)

    # Failsafe should mark test stopped before handling new start click
    res = func.__wrapped__(1, 0, "lab", 0, True, 50.0, "AutoTest", {"mode": "lab"}, {"machine_id": 1}, "feeder")

    assert res == (True, None)

