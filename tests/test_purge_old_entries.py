import os
import csv
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from hourly_data_saving import append_metrics, purge_old_entries, METRICS_FILENAME


def test_purge_old_entries_preserves_header(tmp_path):
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
            "timestamp": "2025-06-28T00:00:00.000000",
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

    append_metrics(metrics, machine_id, export_dir=str(tmp_path))

    # Cleanup should not alter the original header
    purge_old_entries(export_dir=str(tmp_path), machine_id=machine_id)

    with file_path.open() as f:
        rows = list(csv.reader(f))

    assert rows[0] == header

