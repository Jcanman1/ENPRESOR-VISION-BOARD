"""Helper routines for saving hourly metrics and control logs.

These functions create per-machine CSV files and keep only the most
recent 24 hours of data so that historical charts remain manageable.
"""

import os
import csv
from datetime import datetime, timedelta
from typing import Optional, List

EXPORT_DIR = os.path.join(os.path.dirname(__file__), "exports")
METRICS_FILENAME = "last_24h_metrics.csv"
CONTROL_LOG_FILENAME = "last_24h_control_log.csv"



def initialize_data_saving(export_dir: str = EXPORT_DIR,
                           machine_ids: Optional[List[str]] = None):
    """Set up periodic CSV export directory and optional per-machine folders."""
    os.makedirs(export_dir, exist_ok=True)
    if machine_ids:
        for mid in machine_ids:
            os.makedirs(os.path.join(export_dir, str(mid)), exist_ok=True)
    return {"export_dir": export_dir}


def get_historical_data(timeframe: str = "24h", export_dir: str = EXPORT_DIR,
                        machine_id: Optional[str] = None):
    """Return capacity and counter history filtered to the given timeframe."""
    history = load_recent_metrics(export_dir, machine_id=machine_id)

    # Parse the timeframe string like "24h" into an integer hour count
    try:
        hours = int(str(timeframe).rstrip("h"))
    except (ValueError, TypeError):
        hours = 24

    if hours >= 24:
        return history

    cutoff = datetime.now() - timedelta(hours=hours)
    filtered = {
        "capacity": {"times": [], "values": []},
        "accepts": {"times": [], "values": []},
        "rejects": {"times": [], "values": []},
        "running": {"times": [], "values": []},
        "stopped": {"times": [], "values": []},
        **{i: {"times": [], "values": []} for i in range(1, 13)},
    }

    # Filter capacity history
    for t, v in zip(history["capacity"]["times"], history["capacity"]["values"]):
        if t >= cutoff:
            filtered["capacity"]["times"].append(t)
            filtered["capacity"]["values"].append(v)

    # Filter accepts and rejects history
    for key in ("accepts", "rejects"):
        for t, v in zip(history[key]["times"], history[key]["values"]):
            if t >= cutoff:
                filtered[key]["times"].append(t)
                filtered[key]["values"].append(v)

    # Filter running/stopped history
    for key in ("running", "stopped"):
        for t, v in zip(history[key]["times"], history[key]["values"]):
            if t >= cutoff:
                filtered[key]["times"].append(t)
                filtered[key]["values"].append(v)

    # Filter counter history
    for i in range(1, 13):
        for t, v in zip(history[i]["times"], history[i]["values"]):
            if t >= cutoff:
                filtered[i]["times"].append(t)
                filtered[i]["values"].append(v)

    return filtered


def append_metrics(metrics: dict, machine_id: str,
                   export_dir: str = EXPORT_DIR,
                   filename: str = METRICS_FILENAME,
                   mode: Optional[str] = None):
    """Append a row of metrics for a machine and purge old entries.

    A ``mode`` column is added so callers can record whether values were
    captured from a live connection or generated while in demo mode.
    """
    machine_dir = os.path.join(export_dir, str(machine_id))
    os.makedirs(machine_dir, exist_ok=True)
    file_path = os.path.join(machine_dir, filename)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = {"timestamp": timestamp}
    row.update(metrics)
    if mode:
        row["mode"] = mode
    else:
        row["mode"] = ""

    write_header = (
        not os.path.exists(file_path)
        or os.path.getsize(file_path) == 0
    )
    try:
        with open(file_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if write_header:
                writer.writeheader()
            writer.writerow(row)
    except OSError:
        # Skip writing if file is locked by another process
        return

    purge_old_entries(export_dir, machine_id, filename,
                      fieldnames_hint=list(row.keys()))




def purge_old_entries(export_dir: str = EXPORT_DIR, machine_id: Optional[str] = None,
                      filename: str = METRICS_FILENAME, hours: int = 24,
                      fieldnames_hint: Optional[List[str]] = None):
    """Remove CSV rows older than the specified number of hours for a machine."""
    file_path = os.path.join(export_dir, str(machine_id), filename)
    if not os.path.exists(file_path):
        return

    with open(file_path, newline="", encoding="utf-8") as f:
        dict_reader = csv.DictReader(f)
        reader = list(dict_reader)
        detected_fieldnames = dict_reader.fieldnames or []

    if not detected_fieldnames or "timestamp" not in detected_fieldnames:
        # Header missing or corrupted - rebuild using hint or generic names
        with open(file_path, newline="", encoding="utf-8") as f:
            raw_rows = list(csv.reader(f))
        if not raw_rows:
            return
        if fieldnames_hint:
            detected_fieldnames = list(fieldnames_hint)
        else:
            detected_fieldnames = [f"field_{i}" for i in range(len(raw_rows[0]))]
        reader = [dict(zip(detected_fieldnames, r)) for r in raw_rows]
    # Combine detected header with hint
    fieldnames = list(detected_fieldnames)
    if fieldnames_hint:
        for fn in fieldnames_hint:
            if fn not in fieldnames:
                fieldnames.append(fn)

    if not reader:
        return

    # Allow for legacy files without a ``mode`` column
    if "mode" not in fieldnames:
        fieldnames.append("mode")

    cutoff = datetime.now() - timedelta(hours=hours)
    filtered = []
    for row in reader:
        extras = []
        if None in row:
            extras = row.pop(None)
            if not isinstance(extras, list):
                extras = [extras]

        if (
            fieldnames_hint
            and "mode" in fieldnames_hint
            and "mode" in detected_fieldnames
            and fieldnames_hint.index("mode") > detected_fieldnames.index("mode")
            and extras
        ):
            extras = [row.get("mode", "")] + extras
            row["mode"] = ""

        missing = [fn for fn in fieldnames if fn not in row]
        for val, fnm in zip(extras, missing):
            row[fnm] = val
        extras = extras[len(missing):]
        if extras:
            leftover = extras[0]
            if leftover and not row.get("mode"):
                row["mode"] = leftover

        for fn in fieldnames:
            row.setdefault(fn, "")

        try:
            ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
            if ts >= cutoff:
                filtered.append(row)
        except Exception:
            continue

    try:
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(filtered)
    except OSError:
        # Skip cleanup if file is locked
        return


def load_recent_metrics(export_dir: str = EXPORT_DIR, machine_id: Optional[str] = None,
                        filename: str = METRICS_FILENAME):
    """Return counter history from the 24h metrics file for a machine."""
    file_path = os.path.join(export_dir, str(machine_id), filename)
    history = {
        "capacity": {"times": [], "values": []},
        "accepts": {"times": [], "values": []},
        "rejects": {"times": [], "values": []},
        "running": {"times": [], "values": []},
        "stopped": {"times": [], "values": []},
        **{i: {"times": [], "values": []} for i in range(1, 13)},
    }

    if not os.path.exists(file_path):
        return history

    with open(file_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue

            if "capacity" in row and row["capacity"]:
                try:
                    val = float(row["capacity"])
                    history["capacity"]["times"].append(ts)
                    history["capacity"]["values"].append(val)
                except ValueError:
                    pass

            if "accepts" in row and row["accepts"]:
                try:
                    val = float(row["accepts"])
                    history["accepts"]["times"].append(ts)
                    history["accepts"]["values"].append(val)
                except ValueError:
                    pass

            if "rejects" in row and row["rejects"]:
                try:
                    val = float(row["rejects"])
                    history["rejects"]["times"].append(ts)
                    history["rejects"]["values"].append(val)
                except ValueError:
                    pass

            for key in ("running", "stopped"):
                if key in row and row[key]:
                    try:
                        val = float(row[key])
                        history[key]["times"].append(ts)
                        history[key]["values"].append(val)
                    except ValueError:
                        pass

            for i in range(1, 13):
                key = f"counter_{i}"
                if key in row and row[key]:
                    try:
                        val = float(row[key])
                        history[i]["times"].append(ts)
                        history[i]["values"].append(val)
                    except ValueError:
                        pass

    return history



def append_control_log(entry: dict, machine_id: str,
                       export_dir: str = EXPORT_DIR,
                       filename: str = CONTROL_LOG_FILENAME,
                       mode: Optional[str] = None):
    """Append a row of control log data and purge old entries."""
    machine_dir = os.path.join(export_dir, str(machine_id))
    os.makedirs(machine_dir, exist_ok=True)
    file_path = os.path.join(machine_dir, filename)

    timestamp = entry["time"].strftime("%Y-%m-%d %H:%M:%S")
    row = {"timestamp": timestamp}
    for key, value in entry.items():
        if key not in ("time", "display_timestamp"):
            row[key] = value
    row["mode"] = mode if mode else ""

    write_header = (
        not os.path.exists(file_path)
        or os.path.getsize(file_path) == 0
    )

    try:
        with open(file_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if write_header:
                writer.writeheader()
            writer.writerow(row)
    except OSError:
        return

    purge_old_control_entries(export_dir, machine_id, filename,
                              fieldnames_hint=list(row.keys()))


def purge_old_control_entries(export_dir: str = EXPORT_DIR, machine_id: Optional[str] = None,
                              filename: str = CONTROL_LOG_FILENAME, hours: int = 24,
                              fieldnames_hint: Optional[List[str]] = None):
    """Remove control log rows older than the specified hours."""
    purge_old_entries(export_dir, machine_id, filename,
                      hours=hours, fieldnames_hint=fieldnames_hint)


def load_recent_control_log(export_dir: str = EXPORT_DIR, machine_id: Optional[str] = None,
                            filename: str = CONTROL_LOG_FILENAME):
    """Return recent control log entries for a machine."""
    file_path = os.path.join(export_dir, str(machine_id), filename)
    data = []
    if not os.path.exists(file_path):
        return data

    with open(file_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            row["timestamp"] = ts
            data.append(row)

    return data


def get_historical_control_log(timeframe: str = "24h", export_dir: str = EXPORT_DIR,
                               machine_id: Optional[str] = None):
    """Return control log data filtered to the given timeframe.

    Entries are returned newest first regardless of the order stored on disk.
    """
    entries = load_recent_control_log(export_dir, machine_id=machine_id)

    try:
        hours = int(str(timeframe).rstrip("h"))
    except (ValueError, TypeError):
        hours = 24


    if hours < 24:
        cutoff = datetime.now() - timedelta(hours=hours)
        entries = [e for e in entries if e["timestamp"] >= cutoff]

    # Always sort newest first so callers can rely on order
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    return entries


def clear_machine_data(machine_id: str, export_dir: str = EXPORT_DIR):
    """Delete saved metric and control log files for a machine."""
    paths = [
        os.path.join(export_dir, str(machine_id), METRICS_FILENAME),
        os.path.join(export_dir, str(machine_id), CONTROL_LOG_FILENAME),
    ]
    for p in paths:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
