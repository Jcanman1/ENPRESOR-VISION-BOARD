import os
import csv
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from hourly_data_saving import append_metrics, purge_old_entries, METRICS_FILENAME


def test_header_rebuild_with_extra_columns(tmp_path):
    machine_id = "1"
    machine_dir = tmp_path / machine_id
    machine_dir.mkdir(parents=True, exist_ok=True)
    file_path = machine_dir / METRICS_FILENAME

    # Create initial CSV without running/stopped columns
    header = ["timestamp", "capacity", "accepts", "rejects", "mode"]
    with file_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerow({
            "timestamp": "2025-06-28 00:00:00",
            "capacity": "1",
            "accepts": "0",
            "rejects": "0",
            "mode": ""
        })

    metrics = {
        "capacity": 2,
        "accepts": 1,
        "rejects": 0,
        "running": 1,
        "stopped": 0,
    }

    # Append metrics including new columns
    append_metrics(metrics, machine_id, export_dir=str(tmp_path))

    # Ensure cleanup updates header
    purge_old_entries(export_dir=str(tmp_path), machine_id=machine_id,
                      fieldnames_hint=["timestamp"] + list(metrics.keys()) + ["mode"])

    with file_path.open() as f:
        rows = list(csv.reader(f))

    header = rows[0]
    assert "running" in header and "stopped" in header
    data = rows[-1]
    idx_run = header.index("running")
    idx_stop = header.index("stopped")
    assert data[idx_run] == "1"
    assert data[idx_stop] == "0"

