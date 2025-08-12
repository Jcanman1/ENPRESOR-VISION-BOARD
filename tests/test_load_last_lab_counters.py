import callbacks
import hourly_data_saving
import os

def test_load_last_lab_counters(monkeypatch, tmp_path):
    export_dir = tmp_path
    machine_id = 1
    machine_dir = export_dir / str(machine_id)
    machine_dir.mkdir(parents=True)
    log = machine_dir / "Lab_Test_sample.csv"
    log.write_text(
        "timestamp,objects_per_min,objects_60M,counter_1,counter_2\n"
        "2025-01-01T00:00:00,10,100,0,2\n"
        "2025-01-01T00:01:00,20,150,1,3\n"
    )

    monkeypatch.setattr(hourly_data_saving, "EXPORT_DIR", str(export_dir))
    callbacks._lab_totals_cache.clear()

    rates = callbacks.load_last_lab_counters(machine_id)
    assert rates[0] == 1
    assert rates[1] == 3
