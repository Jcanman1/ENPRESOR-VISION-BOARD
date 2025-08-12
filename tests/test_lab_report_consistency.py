import csv
from pathlib import Path

import pandas as pd
import pytest
import dash

import autoconnect
import callbacks
import hourly_data_saving
import generate_report


def _stream_log(csv_path: Path, export_dir: Path) -> None:
    """Append rows from ``csv_path`` to ``Lab_Test_sample.csv`` one at a time."""
    machine_dir = export_dir / "1"
    machine_dir.mkdir(parents=True, exist_ok=True)
    log_path = machine_dir / "Lab_Test_sample.csv"

    with open(csv_path, newline="") as src, open(log_path, "w", newline="") as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=reader.fieldnames)
        writer.writeheader()

        autoconnect.initialize_autoconnect = lambda: None
        app = dash.Dash(__name__)
        callbacks.register_callbacks(app)
        key = next(k for k in app.callback_map if k.startswith("..section-1-1.children"))
        update_1_1 = app.callback_map[key]["callback"]
        key_5_2 = next(k for k in app.callback_map if k.startswith("..section-5-2.children"))
        update_5_2 = app.callback_map[key_5_2]["callback"]

        callbacks.active_machine_id = 1

        for i, row in enumerate(reader, 1):
            writer.writerow(row)
            dst.flush()

            callbacks._lab_totals_cache.clear()
            callbacks._lab_production_cache.clear()

            # Trigger section 1-1 and counter totals so they read the new line
            update_1_1.__wrapped__(
                i,
                "main",
                {},
                {},
                "en",
                {"connected": False},
                {"mode": "lab"},
                {"capacity": 0, "accepts": 0, "rejects": 0},
                {"unit": "lb"},
                {"machines": []},
            )
            update_5_2.__wrapped__(
                i,
                "main",
                {},
                {},
                "en",
                {"connected": False},
                {"mode": "lab"},
                {"machine_id": 1},
                "counts",
            )


def test_section_matches_report(monkeypatch, tmp_path):
    csv_path = Path("Lab_Test_Weaver After SM_08_07_2025.csv")

    # Stream the log into a temporary export directory
    monkeypatch.setattr(hourly_data_saving, "EXPORT_DIR", str(tmp_path))
    callbacks._lab_totals_cache.clear()
    callbacks._lab_production_cache.clear()

    _stream_log(csv_path, tmp_path)

    # Values used by section 1-1
    counts, _, objects = callbacks.load_lab_totals(1)
    cap_lbs, acc_lbs, rej_lbs, _ = callbacks.load_lab_totals_metrics(1)

    sec_objects = objects[-1]
    sec_reject_cnt = sum(counts)
    sec_accept_cnt = sec_objects - sec_reject_cnt

    # Totals as calculated for the PDF report
    df = pd.read_csv(csv_path)
    ts = pd.to_datetime(df["timestamp"])

    obj_stats = generate_report.calculate_total_objects_from_csv_rates(
        df["objects_per_min"], timestamps=ts, is_lab_mode=True
    )

    rej_cnt = 0
    for i in range(1, 13):
        col = f"counter_{i}"
        if col in df.columns:
            rej_cnt += generate_report.calculate_total_objects_from_csv_rates(
                df[col], timestamps=ts, is_lab_mode=True
            )["total_objects"]

    acc_cnt = obj_stats["total_objects"] - rej_cnt

    mult = generate_report.LAB_WEIGHT_MULTIPLIER

    assert cap_lbs == pytest.approx(obj_stats["total_objects"] * mult, rel=1e-3)
    assert acc_lbs == pytest.approx(acc_cnt * mult, rel=1e-3)
    assert rej_lbs == pytest.approx(rej_cnt * mult, rel=1e-3)

    assert sec_objects == pytest.approx(obj_stats["total_objects"], rel=1e-3)
    assert sec_reject_cnt == pytest.approx(rej_cnt, rel=1e-3)
    assert sec_accept_cnt == pytest.approx(acc_cnt, rel=1e-3)

