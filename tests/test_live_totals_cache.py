import csv
import time

import pytest
import callbacks

FIELDS = ["timestamp"] + [f"counter_{i}" for i in range(1, 13)]


def create_csv(tmp_path, rows=1):
    machine_dir = tmp_path / "1"
    machine_dir.mkdir(parents=True, exist_ok=True)
    path = machine_dir / "last_24h_metrics.csv"
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for i in range(rows):
            row = {"timestamp": f"2025-01-01T00:00:{i:02d}"}
            for j in range(1, 13):
                row[f"counter_{j}"] = "1" if j == 1 else "0"
            writer.writerow(row)
    return path


def append_row(path, idx=0):
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        row = {"timestamp": f"2025-01-01T00:00:{idx:02d}"}
        for j in range(1, 13):
            row[f"counter_{j}"] = "1" if j == 1 else "0"
        writer.writerow(row)


def test_append_uses_cached_totals(monkeypatch, tmp_path):
    monkeypatch.setattr(callbacks.hourly_data_saving, "EXPORT_DIR", str(tmp_path))
    callbacks._live_totals_cache.clear()
    path = create_csv(tmp_path, 1)

    totals1 = callbacks.load_live_counter_totals(1)
    id_tot = id(totals1)

    time.sleep(1)
    append_row(path, 1)

    totals2 = callbacks.load_live_counter_totals(1)

    assert id(totals2) == id_tot
    assert totals2[0] == pytest.approx(2)


def test_truncate_resets_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(callbacks.hourly_data_saving, "EXPORT_DIR", str(tmp_path))
    callbacks._live_totals_cache.clear()
    path = create_csv(tmp_path, 2)

    totals1 = callbacks.load_live_counter_totals(1)
    id_tot1 = id(totals1)

    # rewrite file with only one row
    path.unlink()
    create_csv(tmp_path, 1)

    totals2 = callbacks.load_live_counter_totals(1)

    assert id(totals2) != id_tot1
    assert totals2[0] == pytest.approx(1)
