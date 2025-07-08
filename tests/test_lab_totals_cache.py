import os
import csv
import time
import callbacks
import pytest

def create_log(tmp_path):
    machine_dir = tmp_path / "1"
    machine_dir.mkdir(parents=True, exist_ok=True)
    path = machine_dir / "Lab_Test_sample.csv"
    fieldnames = ["timestamp"] + [f"counter_{i}" for i in range(1, 13)]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({"timestamp": "2025-01-01T00:00:00"})
    return path


def test_prune_removes_old_entries():
    callbacks._lab_totals_cache.clear()
    callbacks._lab_totals_cache[1] = {"last_access": time.time() - 10}
    callbacks.prune_lab_totals_cache(max_age=5, max_size=10)
    assert not callbacks._lab_totals_cache


def test_prune_limits_size():
    callbacks._lab_totals_cache.clear()
    now = time.time()
    for i in range(3):
        callbacks._lab_totals_cache[i] = {"last_access": now + i}
    callbacks.prune_lab_totals_cache(max_age=1000, max_size=2)
    assert len(callbacks._lab_totals_cache) == 2
    assert set(callbacks._lab_totals_cache) == {1, 2}


def test_load_lab_totals_calls_prune(monkeypatch, tmp_path):
    monkeypatch.setattr(callbacks.hourly_data_saving, "EXPORT_DIR", str(tmp_path))
    create_log(tmp_path)
    called = []
    monkeypatch.setattr(callbacks, "prune_lab_totals_cache", lambda: called.append(1))
    callbacks.load_lab_totals(1)
    assert called
