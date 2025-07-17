import callbacks
import hourly_data_saving
import os

def test_load_last_lab_objects(monkeypatch, tmp_path):
    # Prepare temp lab log
    export_dir = tmp_path
    machine_id = 1
    machine_dir = export_dir / str(machine_id)
    machine_dir.mkdir(parents=True)
    log = machine_dir / "Lab_Test_sample.csv"
    log.write_text(
        "timestamp,objects_per_min,objects_60M,counter_1\n"
        "2025-01-01T00:00:00,10,100,0\n"
        "2025-01-01T00:01:00,20,150,1\n"
    )

    monkeypatch.setattr(hourly_data_saving, "EXPORT_DIR", str(export_dir))
    callbacks._lab_totals_cache.clear()

    value = callbacks.load_last_lab_objects(machine_id)
    assert value == 150
