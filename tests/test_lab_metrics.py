import os
import csv
import dash

import callbacks
import autoconnect


def setup_app(monkeypatch, tmp_path):
    monkeypatch.setattr(autoconnect, "initialize_autoconnect", lambda: None)
    monkeypatch.setattr(callbacks.hourly_data_saving, "EXPORT_DIR", str(tmp_path))
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    return app


def create_log(tmp_path):
    machine_dir = tmp_path / "1"
    machine_dir.mkdir(parents=True, exist_ok=True)
    path = machine_dir / "Lab_Test_sample.csv"
    fieldnames = [
        "timestamp",
        "capacity",
        "accepts",
        "rejects",
        "objects_per_min",
        "running",
        "stopped",
    ] + [f"counter_{i}" for i in range(1, 13)] + ["mode"]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        row = {
            "timestamp": "2025-01-01T00:00:00",
            "capacity": "100",
            "accepts": "80",
            "rejects": "20",
            "objects_per_min": "60",
            "running": "1",
            "stopped": "0",
            "mode": "Lab",
        }
        for i in range(1, 13):
            row[f"counter_{i}"] = "0"
        writer.writerow(row)
    return path


def test_update_section_1_1_lab_reads_log(monkeypatch, tmp_path):
    app = setup_app(monkeypatch, tmp_path)
    create_log(tmp_path)
    callbacks.active_machine_id = 1
    key = next(k for k in app.callback_map if k.startswith("..section-1-1.children"))
    func = app.callback_map[key]["callback"]

    content, prod = func.__wrapped__(0, "main", {}, {}, "en", {"connected": False}, {"mode": "lab"}, {}, {"unit": "lb"})

    metrics = callbacks.load_lab_totals_metrics(1)
    total_lbs, acc_lbs, rej_lbs, _ = metrics

    expected_cap = callbacks.convert_capacity_from_lbs(total_lbs, {"unit": "lb"})
    expected_acc = callbacks.convert_capacity_from_lbs(acc_lbs, {"unit": "lb"})
    expected_rej = callbacks.convert_capacity_from_lbs(rej_lbs, {"unit": "lb"})

    assert prod["capacity"] == expected_cap
    assert prod["accepts"] == expected_acc
    assert prod["rejects"] == expected_rej

    counter_totals, _, object_totals = callbacks.load_lab_totals(1)
    reject_count = sum(counter_totals)
    capacity_count = object_totals[-1]
    accepts_count = max(0, capacity_count - reject_count)

    unit_label = callbacks.capacity_unit_label({"unit": "lb"})
    unit_label_plain = callbacks.capacity_unit_label({"unit": "lb"}, False)

    cap_text = content.children[1].children[2].children
    acc_text = content.children[2].children[2].children
    rej_text = content.children[3].children[2].children

    assert cap_text == f"{capacity_count:,.0f} pcs / {expected_cap:,.0f} {unit_label}"
    assert acc_text == f"{accepts_count:,.0f} pcs / {expected_acc:,.2f} {unit_label_plain} "
    assert rej_text == f"{reject_count:,.0f} pcs / {expected_rej:,.2f} {unit_label_plain} "


def test_update_section_1_1_lab_no_log(monkeypatch, tmp_path):
    app = setup_app(monkeypatch, tmp_path)
    callbacks.active_machine_id = 1
    key = next(k for k in app.callback_map if k.startswith("..section-1-1.children"))
    func = app.callback_map[key]["callback"]

    content, prod = func.__wrapped__(
        0,
        "main",
        {},
        {},
        "en",
        {"connected": False},
        {"mode": "lab"},
        {},
        {"unit": "lb"},
    )

    unit_label = callbacks.capacity_unit_label({"unit": "lb"})
    unit_label_plain = callbacks.capacity_unit_label({"unit": "lb"}, False)

    cap_text = content.children[1].children[2].children
    acc_text = content.children[2].children[2].children
    rej_text = content.children[3].children[2].children

    assert prod == {"capacity": 0, "accepts": 0, "rejects": 0}
    assert cap_text == f"0 pcs / 0 {unit_label}"
    assert acc_text == f"0 pcs / 0.00 {unit_label_plain} "
    assert rej_text == f"0 pcs / 0.00 {unit_label_plain} "


def test_update_section_1_1_lab_reads_log_connected(monkeypatch, tmp_path):
    app = setup_app(monkeypatch, tmp_path)
    create_log(tmp_path)
    callbacks.active_machine_id = 1
    key = next(k for k in app.callback_map if k.startswith("..section-1-1.children"))
    func = app.callback_map[key]["callback"]

    content, prod = func.__wrapped__(0, "main", {}, {}, "en", {"connected": True}, {"mode": "lab"}, {}, {"unit": "lb"})

    metrics = callbacks.load_lab_totals_metrics(1)
    total_lbs, acc_lbs, rej_lbs, _ = metrics

    expected_cap = callbacks.convert_capacity_from_lbs(total_lbs, {"unit": "lb"})
    expected_acc = callbacks.convert_capacity_from_lbs(acc_lbs, {"unit": "lb"})
    expected_rej = callbacks.convert_capacity_from_lbs(rej_lbs, {"unit": "lb"})

    assert prod["capacity"] == expected_cap
    assert prod["accepts"] == expected_acc
    assert prod["rejects"] == expected_rej

    counter_totals, _, object_totals = callbacks.load_lab_totals(1)
    reject_count = sum(counter_totals)
    capacity_count = object_totals[-1]
    accepts_count = max(0, capacity_count - reject_count)

    unit_label = callbacks.capacity_unit_label({"unit": "lb"})
    unit_label_plain = callbacks.capacity_unit_label({"unit": "lb"}, False)

    cap_text = content.children[1].children[2].children
    acc_text = content.children[2].children[2].children
    rej_text = content.children[3].children[2].children

    assert cap_text == f"{capacity_count:,.0f} pcs / {expected_cap:,.0f} {unit_label}"
    assert acc_text == f"{accepts_count:,.0f} pcs / {expected_acc:,.2f} {unit_label_plain} "
    assert rej_text == f"{reject_count:,.0f} pcs / {expected_rej:,.2f} {unit_label_plain} "


def test_update_section_1_1_lab_no_log_connected(monkeypatch, tmp_path):
    app = setup_app(monkeypatch, tmp_path)
    callbacks.active_machine_id = 1
    key = next(k for k in app.callback_map if k.startswith("..section-1-1.children"))
    func = app.callback_map[key]["callback"]

    content, prod = func.__wrapped__(
        0,
        "main",
        {},
        {},
        "en",
        {"connected": True},
        {"mode": "lab"},
        {},
        {"unit": "lb"},
    )

    unit_label = callbacks.capacity_unit_label({"unit": "lb"})
    unit_label_plain = callbacks.capacity_unit_label({"unit": "lb"}, False)

    cap_text = content.children[1].children[2].children
    acc_text = content.children[2].children[2].children
    rej_text = content.children[3].children[2].children

    assert prod == {"capacity": 0, "accepts": 0, "rejects": 0}
    assert cap_text == f"0 pcs / 0 {unit_label}"
    assert acc_text == f"0 pcs / 0.00 {unit_label_plain} "
    assert rej_text == f"0 pcs / 0.00 {unit_label_plain} "


def test_update_section_1_1_lab_cache_skips_calculation(monkeypatch, tmp_path):
    app = setup_app(monkeypatch, tmp_path)
    create_log(tmp_path)
    callbacks.active_machine_id = 1
    key = next(k for k in app.callback_map if k.startswith("..section-1-1.children"))
    func = app.callback_map[key]["callback"]

    calls = {"metrics": 0, "totals": 0}

    orig_metrics = callbacks.load_lab_totals_metrics
    orig_totals = callbacks.load_lab_totals

    def wrapped_metrics(mid):
        calls["metrics"] += 1
        return orig_metrics(mid)

    def wrapped_totals(mid):
        calls["totals"] += 1
        return orig_totals(mid)

    monkeypatch.setattr(callbacks, "load_lab_totals_metrics", wrapped_metrics)
    monkeypatch.setattr(callbacks, "load_lab_totals", wrapped_totals)

    callbacks._lab_production_cache.clear()
    callbacks._lab_totals_cache.clear()

    func.__wrapped__(0, "main", {}, {}, "en", {"connected": False}, {"mode": "lab"}, {}, {"unit": "lb"})
    assert calls["metrics"] == 1
    assert calls["totals"] == 1

    func.__wrapped__(1, "main", {}, {}, "en", {"connected": False}, {"mode": "lab"}, {}, {"unit": "lb"})
    assert calls["metrics"] == 1
    assert calls["totals"] == 1
