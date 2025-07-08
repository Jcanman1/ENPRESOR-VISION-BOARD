import csv
import time

import callbacks

FIELDS = ["timestamp", "objects_per_min"] + [f"counter_{i}" for i in range(1, 13)]


def create_log(tmp_path, rows=1):
    machine_dir = tmp_path / "1"
    machine_dir.mkdir(parents=True, exist_ok=True)
    path = machine_dir / "Lab_Test_sample.csv"
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for i in range(rows):
            row = {"timestamp": f"2025-01-01T00:00:0{i}", "objects_per_min": "60"}
            for j in range(1, 13):
                row[f"counter_{j}"] = "1" if j == 1 else "0"
            writer.writerow(row)
    return path


def append_row(path, idx=0):
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        row = {"timestamp": f"2025-01-01T00:00:{idx}", "objects_per_min": "60"}
        for j in range(1, 13):
            row[f"counter_{j}"] = "1" if j == 1 else "0"
        writer.writerow(row)


def test_append_uses_cached_totals(monkeypatch, tmp_path):
    monkeypatch.setattr(callbacks.hourly_data_saving, "EXPORT_DIR", str(tmp_path))
    callbacks._lab_totals_cache.clear()
    path = create_log(tmp_path, 1)

    ct1, ts1, obj1 = callbacks.load_lab_totals(1)
    id_ct = id(ct1)
    id_ts = id(ts1)
    id_obj = id(obj1)

    time.sleep(1)
    append_row(path, 1)

    ct2, ts2, obj2 = callbacks.load_lab_totals(1)

    assert id(ct2) == id_ct
    assert id(ts2) == id_ts
    assert id(obj2) == id_obj
    assert ct2[0] == 2


def test_truncate_resets_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(callbacks.hourly_data_saving, "EXPORT_DIR", str(tmp_path))
    callbacks._lab_totals_cache.clear()
    path = create_log(tmp_path, 2)

    ct1, ts1, obj1 = callbacks.load_lab_totals(1)
    id_ct1 = id(ct1)

    # rewrite file with only one row (smaller size)
    path.unlink()
    create_log(tmp_path, 1)

    ct2, ts2, obj2 = callbacks.load_lab_totals(1)

    assert id(ct2) != id_ct1
    assert ct2[0] == 1
