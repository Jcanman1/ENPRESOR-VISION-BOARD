import csv
import argparse
import tempfile
from pathlib import Path

import dash

import callbacks
import autoconnect


def setup_app(export_dir: Path) -> dash.Dash:
    """Create Dash app with callbacks patched for offline use."""
    autoconnect.initialize_autoconnect = lambda: None
    callbacks.hourly_data_saving.EXPORT_DIR = str(export_dir)
    app = dash.Dash(__name__)
    callbacks.register_callbacks(app)
    return app


def simulate(csv_path: Path, delay: float = 0.0) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        export_dir = Path(tmpdir)
        machine_dir = export_dir / "1"
        machine_dir.mkdir(parents=True)
        log_path = machine_dir / "Lab_Test_sample.csv"

        with open(csv_path, newline="") as src, open(log_path, "w", newline="") as dst:
            reader = csv.DictReader(src)
            writer = csv.DictWriter(dst, fieldnames=reader.fieldnames)
            writer.writeheader()

            app = setup_app(export_dir)
            key = next(
                k for k in app.callback_map if k.startswith("..section-1-1.children")
            )
            update_1_1 = app.callback_map[key]["callback"]
            update_5_1 = app.callback_map["section-5-1.children"]["callback"]

            callbacks.active_machine_id = 1

            for i, row in enumerate(reader, 1):
                writer.writerow(row)
                dst.flush()

                callbacks._lab_totals_cache.clear()

                section, _ = update_1_1.__wrapped__(
                    i,
                    "main",
                    {},
                    {},
                    "en",
                    {"connected": False},
                    {"mode": "lab"},
                    {},
                    {"unit": "lb"},
                )
                trend = update_5_1.__wrapped__(
                    i,
                    "main",
                    {},
                    {},
                    "en",
                    {"connected": False},
                    {"mode": "lab"},
                    {"machine_id": 1},
                    {"unit": "lb"},
                    "objects",
                )

                cap_txt = section.children[1].children[2].children
                rej_txt = section.children[3].children[2].children
                obj = trend.children[1].figure.data[0].y[-1]
                print(f"{i:02d} | {cap_txt} | Objects processed: {obj:.2f} | {rej_txt}")

                if delay:
                    import time

                    time.sleep(delay)

            # display final totals
            metrics = callbacks.load_lab_totals_metrics(1)
            counts, _, objects = callbacks.load_lab_totals(1)
            print("Final objects processed:", objects[-1])
            print("Final reject counts:", sum(counts))
            print("Final metrics:", metrics)


def main():
    parser = argparse.ArgumentParser(description="Simulate lab mode using a CSV log")
    parser.add_argument(
        "csv", nargs="?", default="tests/Lab_Test_NEWTEST1_07_07_2025.csv"
    )
    parser.add_argument("--delay", type=float, default=0.0, help="Delay between rows")
    args = parser.parse_args()
    simulate(Path(args.csv), args.delay)


if __name__ == "__main__":
    main()
