"""Dash callback definitions for the modernized OPC dashboard.

This module mirrors much of the behavior from the original monolithic
``EnpresorOPCDataViewBeforeRestructureLegacy`` script.  The callbacks
are registered at runtime via :func:`register_callbacks` so that they
can be imported by both the legacy script and the refactored app.
"""

import importlib
import sys
from datetime import datetime
from collections import defaultdict
import os
import glob
import shutil
import tempfile
import time
import csv
import re
import threading
import hourly_data_saving
import autoconnect
import image_manager as img_utils
import generate_report
from report_tags import save_machine_settings
try:
    import resource
except ImportError:  # pragma: no cover - resource not available on Windows
    resource = None

import memory_monitor as mem_utils

# ``counter_manager`` is imported dynamically from the legacy module, so use
# an alias for the helper functions defined in ``counter_manager.py`` to avoid
# name clashes.
import counter_manager as counter_utils



# Tags for monitoring feeder rate changes - add this near the top of callbacks.py
MONITORED_RATE_TAGS = {
    "Status.Feeders.1Rate": "Feeder 1 Rate",
    "Status.Feeders.2Rate": "Feeder 2 Rate", 
    "Status.Feeders.3Rate": "Feeder 3 Rate",
    "Status.Feeders.4Rate": "Feeder 4 Rate",
}

SENSITIVITY_ACTIVE_TAGS = {
    "Settings.ColorSort.Primary1.IsAssigned": 1,
    "Settings.ColorSort.Primary2.IsAssigned": 2,
    "Settings.ColorSort.Primary3.IsAssigned": 3,
    "Settings.ColorSort.Primary4.IsAssigned": 4,
    "Settings.ColorSort.Primary5.IsAssigned": 5,
    "Settings.ColorSort.Primary6.IsAssigned": 6,
    "Settings.ColorSort.Primary7.IsAssigned": 7,
    "Settings.ColorSort.Primary8.IsAssigned": 8,
    "Settings.ColorSort.Primary9.IsAssigned": 9,
    "Settings.ColorSort.Primary10.IsAssigned": 10,
    "Settings.ColorSort.Primary11.IsAssigned": 11,
    "Settings.ColorSort.Primary12.IsAssigned": 12,
}

# Keep track of which sensitivity tags have already triggered a missing-tag
# warning so we don't flood the log on every update cycle.
warned_sensitivity_tags = set()


def get_active_counter_flags(machine_id):
    """Return a list of booleans indicating which counters are active."""
    flags = [True] * 12
    try:
        states = prev_active_states.get(machine_id, {})
    except Exception:
        states = {}
    for tag, num in SENSITIVITY_ACTIVE_TAGS.items():
        if num <= len(flags):
            val = states.get(tag)
            if val is not None:
                flags[num - 1] = bool(val)
    return flags

# OPC tag for the preset name
PRESET_NAME_TAG = "Status.Info.PresetName"

# Track last logged capacity per machine and filename
last_logged_capacity = defaultdict(lambda: None)

# Filename used for the active lab test session
current_lab_filename = None

# Any metric whose absolute value is below this threshold will be logged as 0.
SMALL_VALUE_THRESHOLD = 1e-3

# Flag to prevent re-entrancy when the legacy module imports this module and
# executes ``register_callbacks`` during import.
_REGISTERING = False

# Cache of lab log totals keyed by ``(machine_id, file_path)``. Each entry
# stores cumulative counter totals, timestamps, object totals and bookkeeping
# information so that subsequent calls only process new rows appended to the
# log file.
_lab_totals_cache = {}


# Cache of live metrics totals keyed by ``(machine_id, file_path)``. Each entry
# stores cumulative counter totals and bookkeeping information so that
# subsequent calls only process new rows appended to the 24h metrics file.
_live_totals_cache = {}

# Cache of lab production metrics keyed by machine id. Stores total capacity,
# accepts, rejects and associated object counts so repeated updates only parse
# new log data.
_lab_production_cache = {}


def _clear_lab_caches(machine_id):
    """Remove cached lab data for the given machine."""
    for key in list(_lab_totals_cache):
        if key[0] == machine_id:
            _lab_totals_cache.pop(key, None)
    _lab_production_cache.pop(machine_id, None)


def _reset_lab_session(machine_id):
    """Reset counters and history for a new lab test."""
    _clear_lab_caches(machine_id)
    global previous_counter_values
    previous_counter_values = [0] * 12
    if "app_state" in globals() and hasattr(app_state, "counter_history"):
        app_state.counter_history = {
            i: {"times": [], "values": []} for i in range(1, 13)
        }


def _create_empty_lab_log(machine_id, filename):
    """Ensure a new lab log file exists so cached data does not reuse old logs."""
    machine_dir = os.path.join(hourly_data_saving.EXPORT_DIR, str(machine_id))
    os.makedirs(machine_dir, exist_ok=True)
    path = os.path.join(machine_dir, filename)
    try:
        with open(path, "w", encoding="utf-8"):
            pass
    except OSError:
        # Ignore failures if file cannot be created
        pass


def _get_latest_lab_file(machine_dir):
    """Return the newest existing ``Lab_Test_*.csv`` file or ``None``."""
    files = glob.glob(os.path.join(machine_dir, "Lab_Test_*.csv"))
    existing = [f for f in files if os.path.exists(f)]
    if not existing:
        return None
    try:
        return max(existing, key=os.path.getmtime)
    except OSError:
        return None





def load_lab_totals(machine_id, filename=None, active_counters=None):
    """Return cumulative counter totals and object totals from a lab log.

    Parameters
    ----------
    machine_id : int
        Identifier for the machine directory under ``EXPORT_DIR``.
    filename : str, optional
        Specific CSV log filename.  If omitted the newest ``Lab_Test_*.csv`` is
        used.
    active_counters : list[bool], optional
        Boolean flags for each counter index ``1-12``.  When provided, only
        counters whose flag is ``True`` contribute to the returned totals.

    The results are cached per file so subsequent calls only process rows that
    were appended since the last invocation. This significantly reduces I/O when
    lab logs grow large.
    """
    machine_dir = os.path.join(hourly_data_saving.EXPORT_DIR, str(machine_id))
    if filename:
        path = os.path.join(machine_dir, filename)
    else:
        path = _get_latest_lab_file(machine_dir)
        if not path:
            return [0] * 12, [], []

    if not os.path.exists(path):
        return [0] * 12, [], []

    key = (machine_id, os.path.abspath(path))
    stat = os.stat(path)
    mtime = stat.st_mtime
    size = stat.st_size

    cache = _lab_totals_cache.get(key)
    if cache is not None:
        # Reset if file was truncated or replaced with an older version
        if size < cache.get("size", 0) or mtime < cache.get("mtime", 0):
            cache = None

    if active_counters is None:
        active_counters = [True] * 12

    if cache is None:
        counter_totals = [0] * 12
        timestamps = []
        object_totals = []
        obj_sum = 0.0
        prev_ts = None
        prev_rate = None
        prev_counters = None

        last_index = -1
    else:
        counter_totals = cache["counter_totals"]
        timestamps = cache["timestamps"]
        object_totals = cache["object_totals"]
        obj_sum = object_totals[-1] if object_totals else 0.0
        prev_ts = cache.get("prev_ts")
        prev_rate = cache.get("prev_rate")
        prev_counters = cache.get("prev_counters")
        last_index = cache.get("last_index", -1)

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            if idx <= last_index:
                continue

            ts = row.get("timestamp")
            ts_val = None
            if ts:
                try:
                    ts_val = datetime.fromisoformat(ts)
                except Exception:
                    ts_val = ts
            timestamps.append(ts_val)


            current_counters = []
            for i in range(1, 13):
                val = row.get(f"counter_{i}")
                try:
                    current_counters.append(float(val) if val else 0.0)
                except ValueError:
                    current_counters.append(0.0)

            if prev_counters is not None:
                if (
                    isinstance(prev_ts, datetime)
                    and isinstance(ts_val, datetime)
                ):
                    delta_minutes = (
                        ts_val - prev_ts
                    ).total_seconds() / 60.0
                else:
                    delta_minutes = 1 / 60.0

                scale = generate_report.LAB_OBJECT_SCALE_FACTOR
                for idx_c, prev_val in enumerate(prev_counters):
                    if idx_c < len(active_counters) and active_counters[idx_c]:
                        counter_totals[idx_c] += prev_val * delta_minutes * scale


            opm = row.get("objects_60M")
            if opm is None or opm == "":
                opm = row.get("objects_per_min")
            try:
                rate_val = float(opm) if opm else None
            except ValueError:
                rate_val = None

            if (
                prev_ts is not None
                and isinstance(prev_ts, datetime)
                and isinstance(ts_val, datetime)
                and prev_rate is not None
            ):
                stats = generate_report.calculate_total_objects_from_csv_rates(
                    [prev_rate, prev_rate],
                    timestamps=[prev_ts, ts_val],
                    is_lab_mode=True,
                )
                obj_sum += stats.get("total_objects", 0)

            object_totals.append(obj_sum)
            prev_ts = ts_val
            prev_rate = rate_val
            prev_counters = current_counters

            last_index = idx

    _lab_totals_cache[key] = {
        "counter_totals": counter_totals,
        "timestamps": timestamps,
        "object_totals": object_totals,
        "last_index": last_index,
        "prev_ts": prev_ts,
        "prev_rate": prev_rate,
        "prev_counters": prev_counters,
        "mtime": mtime,
        "size": size,
    }

    return counter_totals, timestamps, object_totals



def load_live_counter_totals(machine_id, filename=hourly_data_saving.METRICS_FILENAME):
    """Return cumulative counter totals from the live metrics file.

    The results are cached per file so subsequent calls only process rows that
    were appended since the last invocation. This mirrors the caching logic
    used by :func:`load_lab_totals` but only tracks counter totals.
    """
    machine_dir = os.path.join(hourly_data_saving.EXPORT_DIR, str(machine_id))
    path = os.path.join(machine_dir, filename)


    if not os.path.exists(path):
        return [0] * 12

    key = (machine_id, os.path.abspath(path))
    stat = os.stat(path)
    mtime = stat.st_mtime
    size = stat.st_size

    cache = _live_totals_cache.get(key)
    if cache is not None:

        # Reset if file was truncated or replaced with an older version

        if size < cache.get("size", 0) or mtime < cache.get("mtime", 0):
            cache = None

    if cache is None:

        totals = [0] * 12
        last_index = -1
    else:
        totals = cache["totals"]

        last_index = cache.get("last_index", -1)

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            if idx <= last_index:
                continue
            for i in range(1, 13):
                val = row.get(f"counter_{i}")
                try:

                    totals[i - 1] += float(val) if val else 0.0

                except ValueError:
                    pass
            last_index = idx

    _live_totals_cache[key] = {

        "totals": totals,

        "last_index": last_index,
        "mtime": mtime,
        "size": size,
    }


    return totals



def load_last_lab_metrics(machine_id):
    """Return the last capacity/accepts/rejects values from a lab log."""
    machine_dir = os.path.join(hourly_data_saving.EXPORT_DIR, str(machine_id))
    path = _get_latest_lab_file(machine_dir)
    if not path:
        return None
    if not os.path.exists(path):
        return None

    last_row = None
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            last_row = row

    if not last_row:
        return None

    def _get_float(key):
        try:
            return float(last_row.get(key, 0)) if last_row.get(key) else 0.0
        except ValueError:
            return 0.0

    capacity = _get_float("capacity")
    accepts = _get_float("accepts")
    rejects = _get_float("rejects")

    return capacity, accepts, rejects


def load_last_lab_objects(machine_id):
    """Return the most recent ``objects_60M`` value from a lab log."""
    machine_dir = os.path.join(hourly_data_saving.EXPORT_DIR, str(machine_id))
    path = _get_latest_lab_file(machine_dir)
    if not path or not os.path.exists(path):
        return 0

    # Ensure cached data is up to date so ``prev_rate`` reflects the latest row
    load_lab_totals(machine_id)

    key = (machine_id, os.path.abspath(path))
    cache = _lab_totals_cache.get(key)
    if cache:
        rate = cache.get("prev_rate")
        try:
            return float(rate) if rate is not None else 0
        except (ValueError, TypeError):
            return 0

    # Fallback: read the last row directly if cache is missing
    last_row = None
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                last_row = row
    except OSError:
        return 0

    if not last_row:
        return 0

    val = last_row.get("objects_60M") or last_row.get("objects_per_min")
    try:
        return float(val) if val else 0
    except ValueError:
        return 0

def load_last_lab_counters(machine_id):
    """Return the most recent ``counter`` rates from a lab log."""
    machine_dir = os.path.join(hourly_data_saving.EXPORT_DIR, str(machine_id))
    path = _get_latest_lab_file(machine_dir)
    if not path or not os.path.exists(path):
        return [0] * 12

    # Update cached totals so ``prev_counters`` reflects the latest row
    load_lab_totals(machine_id)

    key = (machine_id, os.path.abspath(path))
    cache = _lab_totals_cache.get(key)
    if cache and cache.get("prev_counters") is not None:
        rates = []
        for val in cache.get("prev_counters", [])[:12]:
            try:
                rates.append(float(val))
            except (ValueError, TypeError):
                rates.append(0.0)
        rates.extend([0.0] * (12 - len(rates)))
        return rates

    # Fallback: read the last row directly
    last_row = None
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                last_row = row
    except OSError:
        return [0] * 12

    if not last_row:
        return [0] * 12

    rates = []
    for i in range(1, 13):
        val = last_row.get(f"counter_{i}")
        try:
            rates.append(float(val) if val else 0.0)
        except ValueError:
            rates.append(0.0)
    return rates



def load_lab_average_capacity_and_accepts(machine_id):
    """Return the average capacity rate (lbs/hr), total accepts in lbs,
    and elapsed seconds from the latest lab log."""
    machine_dir = os.path.join(hourly_data_saving.EXPORT_DIR, str(machine_id))
    path = _get_latest_lab_file(machine_dir)
    if not path:
        return None

    capacities = []
    accepts = []
    timestamps = []

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cap = row.get("capacity")
            acc = row.get("accepts")
            ts = row.get("timestamp")
            try:
                if cap:
                    capacities.append(float(cap))
            except ValueError:
                pass
            try:
                accepts.append(float(acc)) if acc else accepts.append(0.0)
            except ValueError:
                accepts.append(0.0)
            if ts:
                timestamps.append(ts)

    stats = generate_report.calculate_total_capacity_from_csv_rates(
        capacities, timestamps=timestamps, is_lab_mode=True
    )
    cap_avg = stats.get("average_rate_lbs_per_hr", 0)
    acc_total = sum(accepts)

    elapsed_seconds = 0
    if timestamps:
        try:
            start = datetime.fromisoformat(str(timestamps[0]))
            end = datetime.fromisoformat(str(timestamps[-1]))
            elapsed_seconds = int((end - start).total_seconds())
        except Exception:
            elapsed_seconds = 0

    return cap_avg, acc_total, elapsed_seconds


def load_lab_totals_metrics(machine_id, active_counters=None):
    """Return total capacity, accepts, rejects and elapsed seconds from the latest lab log.

    ``active_counters`` is accepted for API symmetry with :func:`load_lab_totals`
    but is currently unused.
    """
    machine_dir = os.path.join(hourly_data_saving.EXPORT_DIR, str(machine_id))
    path = _get_latest_lab_file(machine_dir)
    if not path:
        return None

    accepts = []
    rejects = []
    timestamps = []

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            a = row.get("accepts")
            r = row.get("rejects")
            ts = row.get("timestamp")
            try:
                accepts.append(float(a)) if a else accepts.append(0.0)
            except ValueError:
                accepts.append(0.0)
            try:
                rejects.append(float(r)) if r else rejects.append(0.0)
            except ValueError:
                rejects.append(0.0)
            if ts:
                timestamps.append(ts)

    a_stats = generate_report.calculate_total_capacity_from_csv_rates(
        accepts, timestamps=timestamps, is_lab_mode=True
    )
    r_stats = generate_report.calculate_total_capacity_from_csv_rates(
        rejects, timestamps=timestamps, is_lab_mode=True
    )

    accepts_total = a_stats.get("total_capacity_lbs", 0)
    rejects_total = r_stats.get("total_capacity_lbs", 0)
    total_capacity = accepts_total + rejects_total

    elapsed_seconds = 0
    if timestamps:
        try:
            start = datetime.fromisoformat(str(timestamps[0]))
            end = datetime.fromisoformat(str(timestamps[-1]))
            elapsed_seconds = int((end - start).total_seconds())
        except Exception:
            elapsed_seconds = 0

    return total_capacity, accepts_total, rejects_total, elapsed_seconds


def load_live_counter_totals(machine_id):
    """Return total objects removed for each counter from live metrics CSV."""
    file_path = os.path.join(
        hourly_data_saving.EXPORT_DIR,
        str(machine_id),
        hourly_data_saving.METRICS_FILENAME,
    )

    if not os.path.exists(file_path):
        return [0] * 12

    key = (machine_id, os.path.abspath(file_path))
    stat = os.stat(file_path)
    mtime = stat.st_mtime
    size = stat.st_size

    cache = _live_totals_cache.get(key)
    if cache is not None:
        if size < cache.get("size", 0) or mtime < cache.get("mtime", 0):
            cache = None

    if cache is None:
        totals = [0.0] * 12
        last_index = -1
    else:
        totals = cache["totals"]
        last_index = cache["last_index"]

    with open(file_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            if idx <= last_index:
                continue
            for i in range(1, 13):
                val = row.get(f"counter_{i}")
                try:
                    rate = float(val) if val else 0.0
                except ValueError:
                    rate = 0.0
                totals[i - 1] += rate
            last_index = idx

    _live_totals_cache[key] = {
        "totals": totals,
        "last_index": last_index,
        "mtime": mtime,
        "size": size,
    }

    return totals


def refresh_lab_cache(machine_id):
    """Update cached lab totals after a test completes."""
    weight_pref = load_weight_preference()
    machine_dir = os.path.join(hourly_data_saving.EXPORT_DIR, str(machine_id))
    path = _get_latest_lab_file(machine_dir)
    if not path:
        return

    stat = os.stat(path)
    mtime = stat.st_mtime
    size = stat.st_size

    metrics = load_lab_totals_metrics(machine_id, active_counters=get_active_counter_flags(machine_id))
    if not metrics:
        return

    tot_cap_lbs, acc_lbs, rej_lbs, _ = metrics
    counter_totals, _, object_totals = load_lab_totals(
        machine_id, active_counters=get_active_counter_flags(machine_id)
    )

    reject_count = sum(counter_totals)
    capacity_count = object_totals[-1] if object_totals else 0
    accepts_count = max(0, capacity_count - reject_count)

    total_capacity = convert_capacity_from_lbs(tot_cap_lbs, weight_pref)
    accepts = convert_capacity_from_lbs(acc_lbs, weight_pref)
    rejects = convert_capacity_from_lbs(rej_lbs, weight_pref)

    production_data = {
        "capacity": total_capacity,
        "accepts": accepts,
        "rejects": rejects,
    }

    _lab_production_cache[machine_id] = {
        "mtime": mtime,
        "size": size,
        "production_data": production_data,
        "capacity_count": capacity_count,
        "accepts_count": accepts_count,
        "reject_count": reject_count,
    }


def register_callbacks(app):
    """Public entry point that guards against re-entrant registration."""
    global _REGISTERING
    if _REGISTERING:
        return
    _REGISTERING = True
    try:
        _register_callbacks_impl(app)
    finally:
        _REGISTERING = False

def _register_callbacks_impl(app):
    main = sys.modules.get("EnpresorOPCDataViewBeforeRestructureLegacy")
    if main is None:
        candidate = sys.modules.get("__main__")
        if candidate and getattr(candidate, "__file__", "").endswith("EnpresorOPCDataViewBeforeRestructureLegacy.py"):
            main = candidate
        else:
            main = importlib.import_module("EnpresorOPCDataViewBeforeRestructureLegacy")

    sys.modules.setdefault("EnpresorOPCDataViewBeforeRestructureLegacy", main)
    globals().update({k: v for k, v in vars(main).items() if not k.startswith("_")})
    for name in [
        "app_state",
        "machine_connections",
        "connect_and_monitor_machine",
        "load_floor_machine_data",
        "opc_update_thread",
        "auto_reconnection_thread",
        "resume_update_thread",
        "pause_background_processes",
        "resume_background_processes",
        "logger",
    ]:
        if name in globals():
            setattr(autoconnect, name, globals()[name])
    autoconnect.initialize_autoconnect()
    LIVE_LIKE_MODES = {"live", "lab"}

    def format_enpresor(text: str):
        parts = text.split("Enpresor")
        if len(parts) == 2:
            return [
                parts[0],
                html.Span("Enpresor", className="enpresor-font", style={"color": "red"}),
                parts[1],
            ]
        return text

    # Create a client-side callback to handle theme switching
    app.clientside_callback(
        """
        function(theme) {
            console.log('Theme callback triggered with:', theme);

            // Get root document element
            const root = document.documentElement;

            // Define theme colors
            const themeColors = {
                light: {
                    backgroundColor: "#f0f0f0",
                    cardBackgroundColor: "#ffffff",
                    textColor: "#212529",
                    borderColor: "rgba(0,0,0,0.125)",
                    chartBackgroundColor: "rgba(255,255,255,0.9)"
                },
                dark: {
                    backgroundColor: "#202124",
                    cardBackgroundColor: "#2d2d30",
                    textColor: "#e8eaed",
                    borderColor: "rgba(255,255,255,0.125)",
                    chartBackgroundColor: "rgba(45,45,48,0.9)"
                }
            };

            // Apply selected theme
            if (theme === "dark") {
                // Dark mode
                root.style.setProperty("--bs-body-bg", themeColors.dark.backgroundColor);
                root.style.setProperty("--bs-body-color", themeColors.dark.textColor);
                root.style.setProperty("--bs-card-bg", themeColors.dark.cardBackgroundColor);
                root.style.setProperty("--bs-card-border-color", themeColors.dark.borderColor);
                root.style.setProperty("--chart-bg", themeColors.dark.chartBackgroundColor);

                // Add dark-mode class to body for additional CSS targeting
                document.body.classList.add("dark-mode");
                document.body.classList.remove("light-mode");

                // Store theme preference in localStorage
                localStorage.setItem("satake-theme", "dark");
            } else {
                // Light mode (default)
                root.style.setProperty("--bs-body-bg", themeColors.light.backgroundColor);
                root.style.setProperty("--bs-body-color", themeColors.light.textColor);
                root.style.setProperty("--bs-card-bg", themeColors.light.cardBackgroundColor);
                root.style.setProperty("--bs-card-border-color", themeColors.light.borderColor);
                root.style.setProperty("--chart-bg", themeColors.light.chartBackgroundColor);

                // Add light-mode class to body for additional CSS targeting
                document.body.classList.add("light-mode");
                document.body.classList.remove("dark-mode");

                // Store theme preference in localStorage
                localStorage.setItem("satake-theme", "light");
            }

            // Update all Plotly charts with new theme
            if (window.Plotly) {
                const plots = document.querySelectorAll('.js-plotly-plot');
                plots.forEach(plot => {
                    try {
                        const bgColor = theme === "dark" ? themeColors.dark.chartBackgroundColor : themeColors.light.chartBackgroundColor;
                        const textColor = theme === "dark" ? themeColors.dark.textColor : themeColors.light.textColor;

                        Plotly.relayout(plot, {
                            'paper_bgcolor': bgColor,
                            'plot_bgcolor': bgColor,
                            'font.color': textColor
                        });
                    } catch (e) {
                        console.error('Error updating Plotly chart:', e);
                    }
                });

                // Special handling for feeder gauges - update annotation colors specifically
                const feederGauge = document.getElementById('feeder-gauges-graph');
                if (feederGauge && feederGauge.layout && feederGauge.layout.annotations) {
                    try {
                        const labelColor = theme === "dark" ? themeColors.dark.textColor : themeColors.light.textColor;

                        // Update annotation colors (feed rate labels)
                        const updatedAnnotations = feederGauge.layout.annotations.map(annotation => ({
                            ...annotation,
                            font: {
                                ...annotation.font,
                                color: labelColor
                            }
                        }));

                        // Apply the updated annotations
                        Plotly.relayout(feederGauge, {
                            'annotations': updatedAnnotations
                        });

                        console.log('Updated feeder gauge label colors for', theme, 'mode');
                    } catch (e) {
                        console.error('Error updating feeder gauge labels:', e);
                    }
                }
            }

            return theme;
        }
        """,
        Output("theme-selector", "value", allow_duplicate=True),
        Input("theme-selector", "value"),
        prevent_initial_call=True
    )

    @app.callback(
        Output("dashboard-content", "children"),
        [Input("current-dashboard", "data"),
         Input("language-preference-store", "data")]
    )
    def render_dashboard(which, lang):
        if which == "new":
            return render_new_dashboard(lang)
        else:
            return render_main_dashboard(lang)

    @app.callback(
        Output("current-dashboard", "data"),
        Input("new-dashboard-btn", "n_clicks"),
        State("current-dashboard", "data"),
        State("active-machine-store", "data"),  # ADD THIS STATE
        prevent_initial_call=False
    )
    def manage_dashboard(n_clicks, current, active_machine_data):
        """Improved dashboard management that preserves active machine context"""
        # On first load n_clicks is None → show the new dashboard
        if n_clicks is None:
            return "new"
        
        # Allow toggling back to the floor/machine dashboard even when a machine
        # is active.  The previous logic prevented leaving the main dashboard if
        # a machine was selected which made the "Switch Dashboards" button appear
        # unresponsive once a machine was chosen.
        
        # On every actual click, flip between "main" and "new"
        new_dashboard = "new" if current == "main" else "main"
        #logger.debug("manage_dashboard toggled to %s", new_dashboard)
        return new_dashboard

    @app.callback(
        Output("export-data-button", "disabled"),
        [Input("status-update-interval", "n_intervals")],
        [State("active-machine-store", "data")]
    )
    def update_export_button(n_intervals, active_machine_data):
        """Enable or disable the export button based on connection state."""
    
        active_machine_id = active_machine_data.get("machine_id") if active_machine_data else None
        is_connected = (
            active_machine_id
            and active_machine_id in machine_connections
            and machine_connections[active_machine_id].get("connected", False)
        )
    
        return not is_connected

    @app.callback(
        Output("export-download", "data"),
        [Input("export-data-button", "n_clicks")],
        [State("active-machine-store", "data")],
        prevent_initial_call=True,
    )
    def export_all_tags(n_clicks, active_machine_data):
        """Perform full tag discovery and export when the button is clicked."""
        if not n_clicks:
            raise PreventUpdate
    
        active_machine_id = active_machine_data.get("machine_id") if active_machine_data else None
        if (
            not active_machine_id
            or active_machine_id not in machine_connections
            or not machine_connections[active_machine_id].get("connected", False)
        ):
            raise PreventUpdate
    
        pause_update_thread()
        client = machine_connections[active_machine_id]["client"]
        all_tags = run_async(discover_all_tags(client))
        csv_string = generate_csv_string(all_tags)
        resume_update_thread()
    
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        return {
            "content": csv_string,
            "filename": f"satake_data_export_{timestamp_str}.csv",
        }

    @app.callback(
        Output("report-download", "data"),
        Input("generate-report-btn", "n_clicks"),
        [State("app-mode", "data"), State("active-machine-store", "data"), State("language-preference-store", "data")],
        prevent_initial_call=True,
    )
    def generate_report_callback(n_clicks, app_mode, active_machine_data, lang_store):
        """Generate a PDF report when the button is clicked.
        
        FIXED VERSION: The original had a truncated line "if temp" that should be "if temp_dir:"
        Also fixes the hardcoded is_lab_mode=True parameter.
        """
        if not n_clicks:
            raise PreventUpdate

        print("[LAB TEST] Generate report button clicked", flush=True)

        export_dir = generate_report.METRIC_EXPORT_DIR
        lang = lang_store or load_language_preference()
        machines = None
        include_global = True
        temp_dir = None
        lab_test_name = None

        if app_mode and isinstance(app_mode, dict) and app_mode.get("mode") == "lab":
            mid = active_machine_data.get("machine_id") if active_machine_data else None
            if not mid:
                raise PreventUpdate
            machines = [str(mid)]
            include_global = False

            machine_dir = os.path.join(export_dir, str(mid))
            lab_files = glob.glob(os.path.join(machine_dir, "Lab_Test_*.csv"))
            if not lab_files:
                raise PreventUpdate
            latest_file = max(lab_files, key=os.path.getmtime)
            lab_test_name = None
            m = re.match(
                r"Lab_Test_(.+?)_\d{2}_\d{2}_\d{4}(?:_\d{2}_\d{2}_\d{2})?\.csv$",
                os.path.basename(latest_file),
            )
            if m:
                lab_test_name = m.group(1)

            temp_dir = tempfile.mkdtemp()
            temp_machine_dir = os.path.join(temp_dir, str(mid))
            os.makedirs(temp_machine_dir, exist_ok=True)
            shutil.copy(latest_file, os.path.join(temp_machine_dir, "last_24h_metrics.csv"))
            save_machine_settings(
                mid,
                machine_connections,
                export_dir=temp_dir,
                active_only=True,
            )
            export_dir = temp_dir
            data = {}
            is_lab_mode = True  # Set to True only for lab mode
        else:
            data = generate_report.fetch_last_24h_metrics()
            is_lab_mode = False  # Set to False for regular mode

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            print("[LAB TEST] Report generation started", flush=True)
            generate_report.build_report(
                data,
                tmp.name,
                export_dir=export_dir,
                machines=machines,
                include_global=include_global,
                is_lab_mode=is_lab_mode,
                lang=lang,  # pass language
                lab_test_name=lab_test_name if is_lab_mode else None,
            )
            with open(tmp.name, "rb") as f:
                pdf_bytes = f.read()

        print("[LAB TEST] Report generation completed", flush=True)

        # FIXED: Complete the truncated temp directory cleanup
        if temp_dir:  # This was the truncated line: "if temp"
            shutil.rmtree(temp_dir, ignore_errors=True)

        pdf_b64 = base64.b64encode(pdf_bytes).decode()
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        return {
            "content": pdf_b64,
            "filename": f"production_report_{timestamp_str}.pdf",
            "type": "application/pdf",
            "base64": True,
        }

    @app.callback(
        Output("generate-report-btn", "disabled"),
        [Input("status-update-interval", "n_intervals"), Input("lab-test-running", "data")],
        [State("lab-test-stop-time", "data")]
    )
    def disable_report_button(n_intervals, running, stop_time):
        print(
            f"[LAB TEST DEBUG] disable_report_button running={running}, "
            f"stop_time={stop_time}",
            flush=True,
        )
        if running:
            return True
        if stop_time is None:
            return False
        return (time.time() - abs(stop_time)) < 30

    @app.callback(
        [Output("delete-confirmation-modal", "is_open"),
         Output("delete-pending-store", "data"),
         Output("delete-item-details", "children")],
        [Input({"type": "delete-floor-btn", "index": ALL}, "n_clicks"),
         Input({"type": "delete-machine-btn", "index": ALL}, "n_clicks"),
         Input("cancel-delete-btn", "n_clicks"),
         Input("close-delete-modal", "n_clicks")],
        [State("delete-confirmation-modal", "is_open"),
         State({"type": "delete-floor-btn", "index": ALL}, "id"),
         State({"type": "delete-machine-btn", "index": ALL}, "id"),
         State("floors-data", "data"),
         State("machines-data", "data")],
        prevent_initial_call=True
    )
    def handle_delete_confirmation_modal(floor_delete_clicks, machine_delete_clicks, cancel_clicks, close_clicks,
                                       is_open, floor_ids, machine_ids, floors_data, machines_data):
        """Handle opening and closing the delete confirmation modal"""
        ctx = callback_context
        if not ctx.triggered:
            return dash.no_update, dash.no_update, dash.no_update
        
        triggered_prop = ctx.triggered[0]["prop_id"]
        
        # Handle cancel or close buttons
        if "cancel-delete-btn" in triggered_prop or "close-delete-modal" in triggered_prop:
            if cancel_clicks or close_clicks:
                return False, {"type": None, "id": None, "name": None}, ""
        
        # Handle floor delete button clicks
        elif '"type":"delete-floor-btn"' in triggered_prop:
            for i, clicks in enumerate(floor_delete_clicks):
                if clicks and i < len(floor_ids):
                    floor_id = floor_ids[i]["index"]
                    
                    # Find floor name
                    floor_name = f"Floor {floor_id}"
                    if floors_data and floors_data.get("floors"):
                        for floor in floors_data["floors"]:
                            if floor["id"] == floor_id:
                                floor_name = floor["name"]
                                break
                    
                    # Count machines on this floor
                    machine_count = 0
                    if machines_data and machines_data.get("machines"):
                        machine_count = len([m for m in machines_data["machines"] if m.get("floor_id") == floor_id])
                    
                    # Create confirmation message
                    if machine_count > 0:
                        details = html.Div([
                            html.P(f'Floor: "{floor_name}"', className="fw-bold mb-1"),
                            html.P(f"This will also delete {machine_count} machine(s) on this floor.", 
                                  className="text-warning small"),
                            html.P("This action cannot be undone.", className="text-danger small")
                        ])
                    else:
                        details = html.Div([
                            html.P(f'Floor: "{floor_name}"', className="fw-bold mb-1"),
                            html.P("This action cannot be undone.", className="text-danger small")
                        ])
                    
                    return True, {"type": "floor", "id": floor_id, "name": floor_name}, details
        
        # Handle machine delete button clicks  
        elif '"type":"delete-machine-btn"' in triggered_prop:
            for i, clicks in enumerate(machine_delete_clicks):
                if clicks and i < len(machine_ids):
                    machine_id = machine_ids[i]["index"]
                    
                    # Find machine name/details
                    current_lang = load_language_preference()
                    machine_name = f"{tr('machine_label', current_lang)} {machine_id}"
                    machine_details = ""
                    if machines_data and machines_data.get("machines"):
                        for machine in machines_data["machines"]:
                            if machine["id"] == machine_id:
                                serial = machine.get("serial", "Unknown")
                                ip = machine.get("ip", "Unknown")
                                if serial != "Unknown":
                                    machine_details = f"Serial: {serial}"
                                if ip != "Unknown":
                                    if machine_details:
                                        machine_details += f" | IP: {ip}"
                                    else:
                                        machine_details = f"IP: {ip}"
                                break
                    
                    # Create confirmation message
                    details = html.Div([
                        html.P(f"{tr('machine_label', current_lang)}: \"{machine_name}\"", className="fw-bold mb-1"),
                        html.P(machine_details, className="small mb-1") if machine_details else html.Div(),
                        html.P("This action cannot be undone.", className="text-danger small")
                    ])
                    
                    return True, {"type": "machine", "id": machine_id, "name": machine_name}, details
        
        return dash.no_update, dash.no_update, dash.no_update

    @app.callback(
        [Output("system-settings-save-status", "children", allow_duplicate=True),
         Output("weight-preference-store", "data", allow_duplicate=True)],
        [Input("save-system-settings", "n_clicks")],
        [State("auto-connect-switch", "value"),
         State("ip-addresses-store", "data"),
         State("capacity-units-selector", "value"),
         State("custom-unit-name", "value"),
         State("custom-unit-weight", "value")],
        prevent_initial_call=True
    )
    def save_system_settings(n_clicks, auto_connect, ip_addresses,
                             unit_value, custom_name, custom_weight):
        """Save system settings including IP addresses"""
        if not n_clicks:
            return dash.no_update, dash.no_update
        
        # Save system settings
        system_settings = {
            "auto_connect": auto_connect
        }
        
        # Save system settings to file
        try:
            with open('system_settings.json', 'w') as f:
                json.dump(system_settings, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving system settings: {e}")
            return "Error saving system settings", dash.no_update
        
        # Save IP addresses to file - make sure we're getting the full data structure
        try:
            with open('ip_addresses.json', 'w') as f:
                json.dump(ip_addresses, f, indent=4)
            #logger.debug("Saved IP addresses: %s", ip_addresses)
        except Exception as e:
            logger.error(f"Error saving IP addresses: {e}")
            return "Error saving IP addresses", dash.no_update
    
        # Save weight preference
        pref_data = dash.no_update
        if unit_value != "custom":
            save_weight_preference(unit_value, "", 1.0)
            pref_data = {"unit": unit_value, "label": "", "value": 1.0}
        elif custom_name and custom_weight:
            save_weight_preference("custom", custom_name, float(custom_weight))
            pref_data = {"unit": "custom", "label": custom_name,
                         "value": float(custom_weight)}
    
        return "Settings saved successfully", pref_data

    @app.callback(
        [Output("email-settings-save-status", "children"),
         Output("email-settings-store", "data", allow_duplicate=True)],
        Input("save-email-settings", "n_clicks"),
        [State("smtp-server-input", "value"),
         State("smtp-port-input", "value"),
         State("smtp-username-input", "value"),
         State("smtp-password-input", "value"),
         State("smtp-sender-input", "value")],
        prevent_initial_call=True
    )
    def save_email_settings_callback(n_clicks, server, port, username, password, sender):
        """Save SMTP email credentials from the settings modal."""
        if not n_clicks:
            return dash.no_update, dash.no_update
    
        settings = {
            "smtp_server": server or DEFAULT_EMAIL_SETTINGS["smtp_server"],
            "smtp_port": int(port) if port else DEFAULT_EMAIL_SETTINGS["smtp_port"],
            "smtp_username": username or "",
            "smtp_password": password or "",
            "from_address": sender or DEFAULT_EMAIL_SETTINGS["from_address"],
        }
    
        success = save_email_settings(settings)
        if success:
            global email_settings
            email_settings = settings
            return "Email settings saved", settings
        return "Error saving email settings", dash.no_update

    @app.callback(
        Output("settings-modal", "is_open"),
        [
            Input("settings-button", "n_clicks"),
            Input("close-settings", "n_clicks"),
        ],
        [State("settings-modal", "is_open")],
        prevent_initial_call=True
    )
    def toggle_settings_modal(settings_clicks, close_clicks, is_open):
        """Toggle the settings modal"""
        ctx = dash.callback_context
        if not ctx.triggered:
            return dash.no_update
            
        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        
        if trigger_id == "settings-button" and settings_clicks:
            return not is_open
        elif trigger_id == "close-settings" and close_clicks:
            return False

        return is_open

    @app.callback(
        [Output("ip-addresses-store", "data"),
         Output("new-ip-input", "value"),
         Output("new-ip-label", "value"),
         Output("system-settings-save-status", "children")],
        [Input("add-ip-button", "n_clicks")],
        [State("new-ip-input", "value"),
         State("new-ip-label", "value"),
         State("ip-addresses-store", "data")],
        prevent_initial_call=True
    )
    
    def add_ip_address(n_clicks, new_ip, new_label, current_data):
        """Add a new IP address to the stored list"""
        if not n_clicks or not new_ip or not new_ip.strip():
            return dash.no_update, dash.no_update, dash.no_update, dash.no_update
        
        # Use a default label if none provided
        if not new_label or not new_label.strip():
            current_lang = load_language_preference()
            new_label = f"{tr('machine_label', current_lang)} {len(current_data.get('addresses', [])) + 1}"
        
        # Enhanced IP validation to allow localhost formats
        new_ip = new_ip.strip().lower()
        
        # Check for valid localhost formats
        localhost_formats = [
            "localhost",
            "127.0.0.1",
            "::1"  # IPv6 localhost
        ]
        
        is_valid_ip = False
        
        # Check if it's a localhost format
        if new_ip in localhost_formats:
            is_valid_ip = True
            # Normalize localhost to 127.0.0.1 for consistency
            if new_ip == "localhost":
                new_ip = "127.0.0.1"
        else:
            # Check for regular IPv4 format
            ip_parts = new_ip.split('.')
            if len(ip_parts) == 4:
                try:
                    # Validate each part is a number between 0-255
                    if all(part.isdigit() and 0 <= int(part) <= 255 for part in ip_parts):
                        is_valid_ip = True
                except ValueError:
                    pass
            
            # Check for hostname format (letters, numbers, dots, hyphens)
            import re
            hostname_pattern = r'^[a-zA-Z0-9.-]+$'
            if re.match(hostname_pattern, new_ip) and len(new_ip) > 0:
                is_valid_ip = True
        
        if not is_valid_ip:
            return dash.no_update, "", dash.no_update, "Invalid IP address, hostname, or localhost format"
        
        # Get current addresses or initialize empty list
        addresses = current_data.get("addresses", []) if current_data else []
        
        # Check if IP already exists
        ip_already_exists = any(item["ip"] == new_ip for item in addresses)
        if ip_already_exists:
            return dash.no_update, "", dash.no_update, "IP address already exists"
        
        # Add the new IP with label
        addresses.append({"ip": new_ip, "label": new_label})
        
        # Return updated data and clear the inputs
        return {"addresses": addresses}, "", "", "IP address added successfully"

    @app.callback(
        [
            Output("connection-status", "children"),
            Output("connection-status", "className"),
            Output("active-machine-display", "children"),
            Output("active-machine-label", "children"),
            Output("status-label", "children"),
        ],
        [
            Input("status-update-interval", "n_intervals"),
            Input("active-machine-store", "data"),
            Input("language-preference-store", "data"),
        ],
        [
            State("machines-data", "data"),
            State("app-state", "data"),
        ],
        prevent_initial_call=False  # Allow initial call to set default state
    )
    def update_connection_status_display(n_intervals, active_machine_data, lang, machines_data, app_state_data):
        """Update the connection status and active machine display"""
        
        # Get active machine ID
        active_machine_id = active_machine_data.get("machine_id") if active_machine_data else None
        
        if not active_machine_id:
            # No machine selected
            return tr("no_machine_selected", lang), "text-warning small", "None", tr("active_machine_label", lang), tr("status_label", lang)
        
        # Find the active machine details
        machine_info = None
        if machines_data and machines_data.get("machines"):
            for machine in machines_data["machines"]:
                if machine["id"] == active_machine_id:
                    machine_info = machine
                    break
        
        if not machine_info:
            return "Machine not found", "text-danger small", f"{tr('machine_label', lang)} {active_machine_id} (not found)", tr("active_machine_label", lang), tr("status_label", lang)
        
        # Check if this machine is actually connected
        is_connected = (active_machine_id in machine_connections and 
                       machine_connections[active_machine_id].get('connected', False))
        
        # Create machine display text
        serial = machine_info.get('serial', 'Unknown')
        if serial != 'Unknown':
            machine_display = f"{tr('machine_label', lang)} {active_machine_id} (S/N: {serial})"
        else:
            machine_display = f"{tr('machine_label', lang)} {active_machine_id}"
        
        # Determine status
        if is_connected:
            status_text = tr("connected_status", lang)
            status_class = "text-success small"
        else:
            status_text = tr("disconnected_status", lang)
            status_class = "text-warning small"
        return status_text, status_class, machine_display, tr("active_machine_label", lang), tr("status_label", lang)

    @app.callback(
        Output("machines-data", "data", allow_duplicate=True),
        [Input("status-update-interval", "n_intervals"),
         Input("historical-time-index", "data"),
         Input("app-mode", "data")],
        [State("machines-data", "data"),
         State("production-data-store", "data"),
         State("weight-preference-store", "data")],
        prevent_initial_call=True,
    )
    def update_machine_dashboard_data(n_intervals, time_state, app_mode, machines_data, production_data, weight_pref):
        """Update machine data on every interval.
    
        In live mode this checks connection status and pulls fresh values from the
        OPC server.  When running in demo mode we synthesize values matching the
        main dashboard so that all machine cards show changing production data.
        """
        
        if not machines_data or not machines_data.get("machines"):
            return dash.no_update
    
        machines = machines_data.get("machines", [])
        updated = False
    
        mode = "demo"
        if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
            mode = app_mode["mode"]
    
        if mode == "historical":
            hours = time_state.get("hours", 24) if isinstance(time_state, dict) else 24
            for machine in machines:
                machine_id = machine.get("id")
                hist = get_historical_data(timeframe=f"{hours}h", machine_id=machine_id)
                cap_vals = hist.get("capacity", {}).get("values", [])
                acc_vals = hist.get("accepts", {}).get("values", [])
                rej_vals = hist.get("rejects", {}).get("values", [])
                cap_avg_lbs = sum(cap_vals)/len(cap_vals) if cap_vals else 0
                acc_avg_lbs = sum(acc_vals)/len(acc_vals) if acc_vals else 0
                rej_avg_lbs = sum(rej_vals)/len(rej_vals) if rej_vals else 0
                cap_avg = convert_capacity_from_lbs(cap_avg_lbs, weight_pref)
                acc_avg = convert_capacity_from_lbs(acc_avg_lbs, weight_pref)
                rej_avg = convert_capacity_from_lbs(rej_avg_lbs, weight_pref)
                prod = {
                    "capacity_formatted": f"{cap_avg:,.0f}",
                    "accepts_formatted": f"{acc_avg:,.0f}",
                    "rejects_formatted": f"{rej_avg:,.0f}",
                    "diagnostic_counter": (machine.get("operational_data") or {}).get("production", {}).get("diagnostic_counter", "0"),
                }
                if not machine.get("operational_data"):
                    machine["operational_data"] = {"preset": {}, "status": {}, "feeder": {}, "production": prod}
                else:
                    machine["operational_data"].setdefault("production", {})
                    machine["operational_data"]["production"].update(prod)
            machines_data["machines"] = machines
            return machines_data
        
    
        elif mode == "lab":
            # Display metrics from lab logs for each machine
            for machine in machines:
                machine_id = machine.get("id")
                metrics = load_lab_totals_metrics(
                    machine_id, active_counters=get_active_counter_flags(machine_id)
                )
                if metrics:
                    tot_cap_lbs, acc_lbs, rej_lbs, _ = metrics
                    counter_totals, _, object_totals = load_lab_totals(
                        machine_id, active_counters=get_active_counter_flags(machine_id)
                    )
                    reject_count = sum(counter_totals)
                    capacity_count = object_totals[-1] if object_totals else 0
                    accepts_count = max(0, capacity_count - reject_count)

                    cap = convert_capacity_from_lbs(tot_cap_lbs, weight_pref)
                    acc = convert_capacity_from_lbs(acc_lbs, weight_pref)
                    rej = convert_capacity_from_lbs(rej_lbs, weight_pref)
                else:
                    cap = acc = rej = 0
                    capacity_count = accepts_count = reject_count = 0

                prod = {
                    "capacity_formatted": f"{cap:,.0f}",
                    "accepts_formatted": f"{acc:,.0f}",
                    "rejects_formatted": f"{rej:,.0f}",
                    "capacity": cap,
                    "accepts": acc,
                    "rejects": rej,
                    "capacity_count": capacity_count,
                    "accepts_count": accepts_count,
                    "reject_count": reject_count,
                    "diagnostic_counter": (machine.get("operational_data") or {}).get("production", {}).get("diagnostic_counter", "0"),
                }

                if not machine.get("operational_data"):
                    machine["operational_data"] = {"preset": {}, "status": {}, "feeder": {}, "production": prod}
                else:
                    machine["operational_data"].setdefault("production", {})
                    machine["operational_data"]["production"].update(prod)

            machines_data["machines"] = machines
            return machines_data

        elif mode == "demo":
            now_str = datetime.now().strftime("%H:%M:%S")
            new_machines = []
    
            pref = load_weight_preference()
    
            for machine in machines:
                m = machine.copy()
                demo_lbs = random.uniform(47000, 53000)
                cap = convert_capacity_from_kg(demo_lbs / 2.205, pref)
                rej_pct = random.uniform(4.0, 6.0)
                rej = cap * (rej_pct / 100.0)
                acc = cap - rej
    
                counters = [random.randint(10, 180) for _ in range(12)]
    
                m["serial"] = m.get("serial", f"DEMO_{m.get('id')}")
                m["status"] = "DEMO"
                m["model"] = m.get("model", "Enpresor")
                m["last_update"] = now_str
                m["operational_data"] = {
                    "preset": {"number": 1, "name": "Demo"},
                    "status": {"text": "DEMO"},
                    "feeder": {"text": "Running"},
                    "production": {
                        "capacity_formatted": f"{cap:,.0f}",
                        "accepts_formatted": f"{acc:,.0f}",
                        "rejects_formatted": f"{rej:,.0f}",
                        "diagnostic_counter": "0",
                        "capacity": cap,
                        "accepts": acc,
                        "rejects": rej,
                    },
                }
                m["demo_counters"] = counters
                m["demo_mode"] = True
                new_machines.append(m)
    
            machines_data = machines_data.copy()
            machines_data["machines"] = new_machines
            return machines_data
    
    
        # Update ALL machines that should be connected
        for machine in machines:
            machine_id = machine.get("id")
            machine.pop("demo_mode", None)
    
            if machine_id not in machine_connections or not machine_connections.get(machine_id, {}).get('connected', False):
                if machine.get("status") != "Offline":
                    machine["status"] = "Offline"
                    machine["last_update"] = "Never"
                    machine["operational_data"] = None
                    updated = True
                continue
    
            if machine_id in machine_connections:
                try:
                    connection_info = machine_connections[machine_id]
                    
                    # Check if connection is still alive by trying to read a simple tag
                    is_still_connected = False
                    if connection_info.get('connected', False):
                        try:
                            # Try to read the Alive tag or any reliable tag to test connection
                            alive_tag = "Alive"
                            test_successful = False
    
                            if alive_tag in connection_info['tags']:
                                # Try to read the value - if this fails, connection is dead
                                test_value = connection_info['tags'][alive_tag]['node'].get_value()
                                test_successful = True
                            else:
                                # If no Alive tag, try the first available tag
                                for tag_name, tag_info in connection_info['tags'].items():
                                    try:
                                        test_value = tag_info['node'].get_value()
                                        test_successful = True
                                        break  # Success, stop trying other tags
                                    except:
                                        continue  # Try next tag
    
                            if test_successful:
                                is_still_connected = True
                                # Reset failure counter on success
                                connection_info['failure_count'] = 0
                            else:
                                raise Exception("No tags could be read")
    
                        except Exception as e:
                            logger.warning(f"Machine {machine_id} connection test failed: {e}")
                            failure_count = connection_info.get('failure_count', 0) + 1
                            connection_info['failure_count'] = failure_count
                            if failure_count >= FAILURE_THRESHOLD:
                                is_still_connected = False
                                # Mark the connection as dead after repeated failures
                                connection_info['connected'] = False
                            else:
                                # Keep connection alive until threshold reached
                                is_still_connected = True
                    
                    # Update machine status based on actual connection test
                    if is_still_connected:
                        # Connection is good - update with fresh data
                        basic_data = get_machine_current_data(machine_id)
                        operational_data = get_machine_operational_data(machine_id)
                        
                        machine["serial"] = basic_data["serial"]
                        machine["status"] = basic_data["status"]  # This should be "GOOD" for connected machines
                        machine["model"] = basic_data["model"]
                        machine["last_update"] = basic_data["last_update"]
                        machine["operational_data"] = operational_data
                        
                        # IMPORTANT: Ensure status is set to something that indicates connection
                        if machine["status"] in ["Unknown", "Offline", "Connection Lost", "Connection Error"]:
                            machine["status"] = "GOOD"  # Force good status for connected machines
                        
                        updated = True
                        
                    else:
                        # Connection is dead - update status to reflect this
                        machine["status"] = "Connection Lost"
                        machine["last_update"] = "Connection Lost"
                        machine["operational_data"] = None
                        updated = True
                        
                        # Clean up the dead connection
                        try:
                            if connection_info.get('client'):
                                connection_info['client'].disconnect()
                        except:
                            pass  # Ignore errors when disconnecting dead connection
                        
                        # Remove from connections
                        del machine_connections[machine_id]
                        logger.info(f"Removed dead connection for machine {machine_id}")
                        
                except Exception as e:
                    logger.error(f"Error monitoring machine {machine_id}: {e}")
                    # Mark machine as having connection error
                    machine["status"] = "Connection Error"
                    machine["last_update"] = "Error"
                    machine["operational_data"] = None
                    updated = True
                    
                    # Clean up the problematic connection
                    if machine_id in machine_connections:
                        try:
                            if machine_connections[machine_id].get('client'):
                                machine_connections[machine_id]['client'].disconnect()
                        except:
                            pass
                        del machine_connections[machine_id]
        
        if updated:
            machines_data["machines"] = machines
            return machines_data
        
        return dash.no_update

    @app.callback(
        Output("memory-metrics-store", "data"),
        Input("metric-logging-interval", "n_intervals"),
        prevent_initial_call=True,
    )
    def test_memory_management(_):
        """Return memory usage metrics for tests and enforce history limits."""
        max_points = 120
        if hasattr(app_state, "counter_history"):
            for i in range(1, 13):
                history = app_state.counter_history[i]
                if len(history["times"]) > max_points:
                    history["times"] = history["times"][-max_points:]
                    history["values"] = history["values"][-max_points:]
            lengths = {
                i: len(app_state.counter_history[i]["times"]) for i in range(1, 13)
            }
        else:
            lengths = {}

        rss_mb = mem_utils._get_process_memory_mb()
        if rss_mb == 0.0:
            rss_mb = 0.0
        return {"rss_mb": rss_mb, "max_points": max_points, "history_lengths": lengths}

    @app.callback(
        Output("saved-ip-list", "children"),
        [Input("ip-addresses-store", "data")]
    )
    def update_saved_ip_list(ip_data):
        """Update the list of saved IPs displayed in settings"""
        if not ip_data or "addresses" not in ip_data or not ip_data["addresses"]:
            return html.Div("No IP addresses saved", className="text-muted fst-italic")
        
        # Create a list item for each saved IP
        ip_items = []
        for item in ip_data["addresses"]:
            ip = item["ip"]
            label = item["label"]
            # Display format for the list: "Label: IP"
            display_text = f"{label}: {ip}"
            
            ip_items.append(
                dbc.Row([
                    dbc.Col(display_text, width=9),
                    dbc.Col(
                        dbc.Button(
                            "×", 
                            id={"type": "delete-ip-button", "index": ip},  # Still use IP as index for deletion
                            color="danger",
                            size="sm",
                            className="py-0 px-2"
                        ),
                        width=3,
                        className="text-end"
                    )
                ], className="mb-2 border-bottom pb-2")
            )
        
        return html.Div(ip_items)

    @app.callback(
        [Output("current-dashboard", "data", allow_duplicate=True),
        Output("active-machine-store", "data"),
        Output("app-state", "data", allow_duplicate=True)],
        [Input({"type": "machine-card-click", "index": ALL}, "n_clicks")],
        [State("machines-data", "data"),
        State("active-machine-store", "data"),
        State("app-state", "data"),
        State({"type": "machine-card-click", "index": ALL}, "id")],
        prevent_initial_call=True
    )
    def handle_machine_selection(card_clicks, machines_data, active_machine_data, app_state_data, card_ids):
        """Handle machine card clicks and switch to main dashboard - FIXED VERSION"""
        global active_machine_id, machine_connections, app_state
        
        ctx = callback_context
        if not ctx.triggered:
            return dash.no_update, dash.no_update, dash.no_update

        # Ignore spurious triggers when the layout re-renders
        if not any(card_clicks):
            raise PreventUpdate

        triggered_id = ctx.triggered_id
        machine_id = None
        if isinstance(triggered_id, dict) and triggered_id.get("type") == "machine-card-click":
            machine_id = triggered_id.get("index")

        if machine_id is None:
            #logger.warning("Machine card clicked but no machine ID found")
            return dash.no_update, dash.no_update, dash.no_update
        
        # CRITICAL FIX: Set global active_machine_id FIRST
        active_machine_id = machine_id
        #logger.info(f"=== MACHINE SELECTION: Selected machine {machine_id} as active machine ===")
        
        # CRITICAL FIX: Stop existing thread before starting new one
        if app_state.update_thread is not None and app_state.update_thread.is_alive():
            #logger.info("Stopping existing OPC update thread...")
            app_state.thread_stop_flag = True
            app_state.update_thread.join(timeout=3)
            #if app_state.update_thread.is_alive():
                #logger.warning("Thread did not stop gracefully")
            #else:
                #logger.info("Successfully stopped existing OPC update thread")
        
        # Check if the machine is connected
        if machine_id in machine_connections and machine_connections[machine_id].get('connected', False):
            # Machine is connected - set up app_state to point to this machine's data
            connection_info = machine_connections[machine_id]
            
            app_state.client = connection_info['client']
            app_state.tags = connection_info['tags']
            app_state.connected = True
            app_state.last_update_time = connection_info.get('last_update', datetime.now())
            
            # Start fresh thread for the selected machine
            app_state.thread_stop_flag = False
            app_state.update_thread = Thread(target=opc_update_thread)
            app_state.update_thread.daemon = True
            app_state.update_thread.start()
            #logger.info(f"Started new OPC update thread for machine {machine_id}")
            #logger.debug(
            #    "Thread status after selection: mode=%s, active_machine=%s, alive=%s",
            #    current_app_mode,
            #    active_machine_id,
            #    app_state.update_thread.is_alive(),
            #)
            
            #logger.info(f"Switched to connected machine {machine_id} - {len(app_state.tags)} tags available")
            app_state_data["connected"] = True
            
        else:
            # Machine not connected
            app_state.client = None
            app_state.tags = {}
            app_state.connected = False
            app_state.last_update_time = None
            
            #logger.info(f"Switched to disconnected machine {machine_id}")
            app_state_data["connected"] = False
        
        # Return to main dashboard with selected machine
        #logger.info(f"=== SWITCHING TO MAIN DASHBOARD with machine {machine_id} ===")
        return "main", {"machine_id": machine_id}, app_state_data

    @app.callback(
        Output("machines-data", "data", allow_duplicate=True),
        [Input({"type": "machine-connect-btn", "index": ALL}, "n_clicks")],
        [State("machines-data", "data"),
        State({"type": "machine-ip-dropdown", "index": ALL}, "value"),
        State({"type": "machine-connect-btn", "index": ALL}, "id"),
        State("server-name-input", "value")],
        prevent_initial_call=True
    )
    def handle_machine_connect_disconnect(n_clicks_list, machines_data, ip_values, button_ids, server_name):
        """Handle connect/disconnect - IMPROVED VERSION with better thread management"""
        
        if not any(n_clicks_list) or not button_ids:
            return dash.no_update
        
        # Find which button was clicked
        triggered_idx = None
        for i, clicks in enumerate(n_clicks_list):
            if clicks is not None and clicks > 0:
                triggered_idx = i
                break
        
        if triggered_idx is None:
            return dash.no_update
        
        machine_id = button_ids[triggered_idx]["index"]
        selected_ip = ip_values[triggered_idx] if triggered_idx < len(ip_values) else None
        
        if not selected_ip:
            return dash.no_update
        
        machines = machines_data.get("machines", [])
        is_connected = machine_id in machine_connections and machine_connections[machine_id]['connected']
        
        if is_connected:
            # DISCONNECT
            try:
                if machine_id in machine_connections:
                    machine_connections[machine_id]['client'].disconnect()
                    del machine_connections[machine_id]
                    #logger.info(f"Disconnected machine {machine_id}")
                
                for machine in machines:
                    if machine["id"] == machine_id:
                        machine["status"] = "Offline"
                        machine["last_update"] = "Disconnected"
                        machine["operational_data"] = None
                        break
                        
            except Exception as e:
                logger.error(f"Error disconnecting machine {machine_id}: {e}")
        
        else:
            # CONNECT
            try:
                connection_success = run_async(connect_and_monitor_machine(selected_ip, machine_id, server_name))
                
                if connection_success:
                    machine_data = get_machine_current_data(machine_id)
                    operational_data = get_machine_operational_data(machine_id)
                    
                    for machine in machines:
                        if machine["id"] == machine_id:
                            machine["ip"] = selected_ip
                            machine["selected_ip"] = selected_ip
                            machine["serial"] = machine_data["serial"]
                            machine["status"] = machine_data["status"]
                            machine["model"] = machine_data["model"]
                            machine["last_update"] = machine_data["last_update"]
                            machine["operational_data"] = operational_data
                            break
                            
                    #logger.info(f"Successfully connected machine {machine_id}")

                    # Initialize previous values so the next change will be logged
                    if machine_id not in prev_values:
                        prev_values[machine_id] = {}
                    if machine_id not in prev_active_states:
                        prev_active_states[machine_id] = {}
                    if machine_id not in prev_preset_names:
                        prev_preset_names[machine_id] = None

                    tags = machine_connections[machine_id]["tags"]
                    for opc_tag in MONITORED_RATE_TAGS:
                        if opc_tag in tags:
                            prev_values[machine_id][opc_tag] = tags[opc_tag]["data"].latest_value
                    for opc_tag in SENSITIVITY_ACTIVE_TAGS:
                        if opc_tag in tags:
                            prev_active_states[machine_id][opc_tag] = tags[opc_tag]["data"].latest_value
                    if PRESET_NAME_TAG in tags:
                        prev_preset_names[machine_id] = tags[PRESET_NAME_TAG]["data"].latest_value
                    
                    # IMPROVED: Only start thread if no machines are currently active
                    # If this is the first connection or the current active machine
                    if active_machine_id == machine_id or active_machine_id is None:
                        if app_state.update_thread is None or not app_state.update_thread.is_alive():
                            app_state.thread_stop_flag = False
                            app_state.update_thread = Thread(target=opc_update_thread)
                            app_state.update_thread.daemon = True
                            app_state.update_thread.start()
                            #logger.info("Started OPC update thread for connected machine")
                    
                else:
                    logger.error(f"Failed to connect machine {machine_id}")
                    
            except Exception as e:
                logger.error(f"Error connecting machine {machine_id}: {e}")
        
        machines_data["machines"] = machines
        return machines_data

    @app.callback(
        Output("delete-ip-trigger", "data"),
        [Input({"type": "delete-ip-button", "index": ALL}, "n_clicks")],
        [State({"type": "delete-ip-button", "index": ALL}, "id")],
        prevent_initial_call=True
    )
    def handle_delete_button(n_clicks_list, button_ids):
        """Capture which delete button was clicked"""
        ctx = dash.callback_context
        if not ctx.triggered:
            return dash.no_update
        
        # Get which button was clicked by finding the button with a non-None click value
        triggered_idx = None
        for i, clicks in enumerate(n_clicks_list):
            if clicks is not None:
                triggered_idx = i
                break
        
        if triggered_idx is None:
            return dash.no_update
        
        # Get the corresponding button id
        button_id = button_ids[triggered_idx]
        ip_to_delete = button_id["index"]  # This is already a dictionary, no need for json.loads
        
        # Return the IP to delete
        return {"ip": ip_to_delete, "timestamp": time.time()}

    @app.callback(
        [Output("ip-addresses-store", "data", allow_duplicate=True),
         Output("delete-result", "children")],
        [Input("delete-ip-trigger", "data")],
        [State("ip-addresses-store", "data")],
        prevent_initial_call=True
    )
    def delete_ip_address(trigger_data, current_data):
        """Delete an IP address from the stored list"""
        if not trigger_data or "ip" not in trigger_data:
            return dash.no_update, dash.no_update
        
        ip_to_delete = trigger_data["ip"]
        
        # Get current addresses
        addresses = current_data.get("addresses", []) if current_data else []
        
        # Find the item to delete by IP
        found = False
        for i, item in enumerate(addresses):
            if item["ip"] == ip_to_delete:
                # Get the label for the message
                label = item["label"]
                # Remove the item
                addresses.pop(i)
                message = f"Deleted {label} ({ip_to_delete})"
                found = True
                break
        
        if not found:
            message = "IP address not found"
        
        # Return updated data
        return {"addresses": addresses}, message

    @app.callback(
        Output("theme-selector", "value"),
        [Input("auto-connect-trigger", "data")],
        prevent_initial_call=False
    )
    def load_initial_theme(trigger):
        """Load theme preference from file on startup"""
        theme = load_theme_preference()
        logger.info(f"Loading initial theme: {theme}")
        return theme


    @app.callback(
        [Output("capacity-units-selector", "value"),
         Output("custom-unit-name", "value"),
         Output("custom-unit-weight", "value")],
        [Input("auto-connect-trigger", "data")],
        prevent_initial_call=False,
    )
    def load_initial_capacity_units(trigger):
        pref = load_weight_preference()
        return pref.get("unit", "lb"), pref.get("label", ""), pref.get("value", 1.0)

    @app.callback(
        [Output("custom-unit-name", "style"),
         Output("custom-unit-weight", "style")],
        [Input("capacity-units-selector", "value")],
        prevent_initial_call=False,
    )
    def toggle_custom_unit_fields(unit_value):
        if unit_value == "custom":
            return {"display": "block"}, {"display": "block"}
        return {"display": "none"}, {"display": "none"}

    @app.callback(
        Output("weight-preference-store", "data"),
        [Input("capacity-units-selector", "value"),
         Input("custom-unit-name", "value"),
         Input("custom-unit-weight", "value")],
        prevent_initial_call=True,
    )
    def save_capacity_units(unit_value, custom_name, custom_weight):
        if unit_value != "custom":
            save_weight_preference(unit_value, "", 1.0)
            return {"unit": unit_value, "label": "", "value": 1.0}
        if custom_name and custom_weight:
            save_weight_preference("custom", custom_name, float(custom_weight))
            return {"unit": "custom", "label": custom_name, "value": float(custom_weight)}
        # If custom selected but fields incomplete, don't update
        return dash.no_update

    @app.callback(
        Output("language-selector", "value"),
        [Input("auto-connect-trigger", "data")],
        prevent_initial_call=False,
    )
    def load_initial_language(trigger):
        return load_language_preference()

    @app.callback(
        Output("language-preference-store", "data"),
        [Input("language-selector", "value")],
        prevent_initial_call=True,
    )
    def save_language(value):
        if value:
            save_language_preference(value)
            return value
        return dash.no_update

    @app.callback(
        Output("dashboard-title", "children"),
        [Input("active-machine-store", "data"),
         Input("current-dashboard", "data"),
         Input("language-preference-store", "data")],
        [State("machines-data", "data")],
        prevent_initial_call=True
    )
    def update_dashboard_title(active_machine_data, current_dashboard, lang, machines_data):
        """Update dashboard title to show active machine"""
        base_title = format_enpresor(tr("dashboard_title", lang))
        base_list = base_title if isinstance(base_title, list) else [base_title]

        if current_dashboard == "main" and active_machine_data and active_machine_data.get("machine_id"):
            machine_id = active_machine_data["machine_id"]
            
            # Find machine details
            machine_name = f"{tr('machine_label', lang)} {machine_id}"
            if machines_data and machines_data.get("machines"):
                for machine in machines_data["machines"]:
                    if machine["id"] == machine_id:
                        serial = machine.get("serial", "Unknown")
                        if serial != "Unknown":
                            machine_name = f"{tr('machine_label', lang)} {machine_id} (S/N: {serial})"
                        break
            
            return base_list + [f" - {machine_name}"]

        return base_title

    @app.callback(
        [Output("threshold-modal-header", "children"),
         Output("display-modal-header", "children"),
         Output("display-modal-description", "children"),
         Output("close-threshold-settings", "children"),
         Output("save-threshold-settings", "children"),
         Output("close-display-settings", "children"),
         Output("save-display-settings", "children"),
         Output("production-rate-units-header", "children"),
         Output("close-production-rate-units", "children"),
         Output("save-production-rate-units", "children"),
         Output("settings-modal-header", "children"),
         Output("update-counts-header", "children"),
         Output("close-update-counts", "children"),
         Output("upload-modal-header", "children"),
         Output("close-upload-modal", "children"),
         Output("delete-confirmation-header", "children"),
         Output("delete-warning", "children"),
         Output("cancel-delete-btn", "children"),
         Output("confirm-delete-btn", "children"),
         Output("close-settings", "children"),
        Output("add-floor-btn", "children"),
        Output("export-data-button", "children"),
        Output("new-dashboard-btn", "children"),
        Output("generate-report-btn", "children"),
        Output("color-theme-label", "children"),
        Output("theme-selector", "options"),
        Output("capacity-units-label", "children"),
        Output("language-label", "children"),
        Output("language-selector", "options"),
        Output("mode-selector", "options"),
        Output("system-configuration-title", "children"),
         Output("auto-connect-label", "children"),
         Output("add-machine-ip-label", "children"),
         Output("smtp-email-configuration-title", "children"),
         Output("smtp-server-label", "children"),
         Output("smtp-port-label", "children"),
         Output("smtp-username-label", "children"),
         Output("smtp-password-label", "children"),
         Output("smtp-from-label", "children"),
        Output("save-email-settings", "children"),
        Output("production-rate-unit-selector", "options"),
        Output("display-tab", "label"),
        Output("system-tab", "label"),
        Output("email-tab", "label"),
        Output("about-tab", "label"),
        Output("start-test-btn", "children"),
        Output("stop-test-btn", "children"),
        Output("lab-test-name", "placeholder"),
        Output("lab-start-selector", "options"),
        Output("upload-image", "children"),
        Output("add-ip-button", "children"),
        Output("save-system-settings", "children")],
        [Input("language-preference-store", "data")]
    )
    def refresh_text(lang):
        return (
            tr("threshold_settings_title", lang),
            tr("display_settings_title", lang),
            tr("display_settings_header", lang),
            tr("close", lang),
            tr("save_changes", lang),
            tr("close", lang),
            tr("save_changes", lang),
            tr("production_rate_units_title", lang),
            tr("close", lang),
            tr("save", lang),
            tr("system_settings_title", lang),
            tr("update_counts_title", lang),
            tr("close", lang),
            tr("upload_image_title", lang),
            tr("close", lang),
            tr("confirm_deletion_title", lang),
            tr("delete_warning", lang),
            tr("cancel", lang),
            tr("yes_delete", lang),
            tr("close", lang),
            tr("add_floor", lang),
            tr("export_data", lang),
            tr("switch_dashboards", lang),
            tr("generate_report", lang),
            tr("color_theme_label", lang),
            [
                {"label": tr("light_mode_option", lang), "value": "light"},
                {"label": tr("dark_mode_option", lang), "value": "dark"},
            ],
            tr("capacity_units_label", lang),
            tr("language_label", lang),
            [
                {"label": tr("english_option", lang), "value": "en"},
                {"label": tr("spanish_option", lang), "value": "es"},
                {"label": tr("japanese_option", lang), "value": "ja"},
            ],
            [
                {"label": tr("live_mode_option", lang), "value": "live"},
                {"label": tr("demo_mode_option", lang), "value": "demo"},
                {"label": tr("historical_mode_option", lang), "value": "historical"},
                {"label": tr("lab_test_mode_option", lang), "value": "lab"},
            ],
            tr("system_configuration_title", lang),
            tr("auto_connect_label", lang),
            tr("add_machine_ip_label", lang),
            tr("smtp_email_configuration_title", lang),
            tr("smtp_server_label", lang),
            tr("port_label", lang),
            tr("username_label", lang),
            tr("password_label", lang),
            tr("from_address_label", lang),
            tr("save_email_settings", lang),
            [
                {"label": tr("objects_per_min", lang), "value": "objects"},
                {"label": tr("capacity", lang), "value": "capacity"},
            ],
            tr("display_tab_label", lang),
            tr("system_tab_label", lang),
            tr("email_tab_label", lang),
            tr("about_tab_label", lang),
            tr("start_test", lang),
            tr("stop_test", lang),
            tr("test_lot_name_placeholder", lang),
            [
                {"label": tr("local_start_option", lang), "value": "local"},
                {"label": tr("feeder_start_option", lang), "value": "feeder"},
            ],
            html.Div([
                tr("drag_and_drop", lang),
                html.A(tr("select_image", lang))
            ]),
            tr("add_button", lang),
            tr("save_system_settings", lang),
        )

    @app.callback(
        Output("hidden-machines-cache", "data"),
        [Input("machines-data", "data")],
        prevent_initial_call=True
    )
    def cache_machines_data(machines_data):
        """Cache machines data for auto-reconnection thread"""
        if machines_data:
            app_state.machines_data_cache = machines_data
            #logger.debug(f"Cached machines data: {len(machines_data.get('machines', []))} machines")
        return machines_data

    @app.callback(
        Output("floor-machine-container", "children"),
        [Input("machines-data", "data"),
         Input("floors-data", "data"),
         Input("ip-addresses-store", "data"),
         Input("additional-image-store", "data"),
         Input("current-dashboard", "data"),
         Input("active-machine-store", "data"),
         Input("app-mode", "data"),
         Input("language-preference-store", "data")],
        prevent_initial_call=False
    )
    def render_floor_machine_layout_enhanced_with_selection(machines_data, floors_data, ip_addresses_data, additional_image_data, current_dashboard, active_machine_data, app_mode_data, lang):
        """Enhanced render with machine selection capability"""
        
        # CRITICAL: Only render on machine dashboard
        if current_dashboard != "new":
            raise PreventUpdate
        
        # ADD THIS CHECK: Prevent re-render if only machine status/operational data changed
        ctx = callback_context
        if ctx.triggered:
            trigger_id = ctx.triggered[0]["prop_id"]
            if "machines-data" in trigger_id:
                # Check if any floor is currently being edited
                if floors_data and floors_data.get("floors"):
                    for floor in floors_data["floors"]:
                        if floor.get("editing", False):
                            # A floor is being edited, don't re-render
                            return dash.no_update
        
        # Rest of the function continues as normal...
        active_machine_id = active_machine_data.get("machine_id") if active_machine_data else None
        
        return render_floor_machine_layout_with_customizable_names(
            machines_data,
            floors_data,
            ip_addresses_data,
            additional_image_data,
            current_dashboard,
            active_machine_id,
            app_mode_data,
            lang,
        )

    @app.callback(
        [Output("floors-data", "data", allow_duplicate=True),
         Output("machines-data", "data", allow_duplicate=True),
         Output("delete-confirmation-modal", "is_open", allow_duplicate=True)],
        [Input("confirm-delete-btn", "n_clicks")],
        [State("delete-pending-store", "data"),
         State("floors-data", "data"),
         State("machines-data", "data")],
        prevent_initial_call=True
    )
    def execute_confirmed_deletion(confirm_clicks, pending_delete, floors_data, machines_data):
        """Execute the deletion after user confirms"""
        global machine_connections, current_lab_filename
        
        if not confirm_clicks or not pending_delete or pending_delete.get("type") is None:
            return dash.no_update, dash.no_update, dash.no_update
        
        delete_type = pending_delete.get("type")
        delete_id = pending_delete.get("id")
        
        if delete_type == "floor":
            # Execute floor deletion (your existing floor deletion logic)
            floors = floors_data.get("floors", [])
            machines = machines_data.get("machines", [])
            
            # Find the floor to delete
            floor_found = False
            floor_name = None
            updated_floors = []
            
            for floor in floors:
                if floor["id"] == delete_id:
                    floor_found = True
                    floor_name = floor.get("name", f"Floor {delete_id}")
                    logger.info(f"Deleting floor: {floor_name}")
                else:
                    updated_floors.append(floor)
            
            if not floor_found:
                logger.warning(f"Floor {delete_id} not found for deletion")
                return dash.no_update, dash.no_update, False
            
            # Find machines on this floor and disconnect them
            machines_on_floor = [m for m in machines if m.get("floor_id") == delete_id]
            machines_to_keep = [m for m in machines if m.get("floor_id") != delete_id]
            
            # Disconnect machines on this floor
            for machine in machines_on_floor:
                machine_id = machine["id"]
                try:
                    if machine_id in machine_connections:
                        if machine_connections[machine_id].get('connected', False):
                            client = machine_connections[machine_id].get('client')
                            if client:
                                client.disconnect()
                            logger.info(f"Disconnected machine {machine_id} before floor deletion")
                        del machine_connections[machine_id]
                        logger.info(f"Removed machine {machine_id} from connections")
                except Exception as e:
                    logger.error(f"Error disconnecting machine {machine_id} during floor deletion: {e}")
            
            # Update data structures
            floors_data["floors"] = updated_floors
            machines_data["machines"] = machines_to_keep
            
            # Update selected floor if needed
            if floors_data.get("selected_floor") == delete_id:
                floors_data["selected_floor"] = "all" if updated_floors else 1
                logger.info(f"Changed selected floor to {floors_data['selected_floor']} after deletion")
            
            # Auto-save
            try:
                save_success = save_floor_machine_data(floors_data, machines_data)
                if save_success:
                    logger.info(f"Successfully deleted floor '{floor_name}' with {len(machines_on_floor)} machines and saved layout")
                else:
                    logger.warning(f"Floor '{floor_name}' deleted but layout save failed")
            except Exception as e:
                logger.error(f"Error saving layout after deleting floor '{floor_name}': {e}")
            
            return floors_data, machines_data, False
            
        elif delete_type == "machine":
            # Execute machine deletion (your existing machine deletion logic)
            machines = machines_data.get("machines", [])
            
            # Find and remove the machine
            machine_found = False
            updated_machines = []
            
            for machine in machines:
                if machine["id"] == delete_id:
                    machine_found = True
                    
                    # Disconnect the machine if connected
                    try:
                        if delete_id in machine_connections:
                            if machine_connections[delete_id].get('connected', False):
                                client = machine_connections[delete_id].get('client')
                                if client:
                                    client.disconnect()
                                logger.info(f"Disconnected machine {delete_id} before deletion")
                            del machine_connections[delete_id]
                            logger.info(f"Removed machine {delete_id} from connections")
                    except Exception as e:
                        logger.error(f"Error disconnecting machine {delete_id}: {e}")
                    
                    logger.info(f"Deleted machine {delete_id}: {machine.get('name', 'Unknown')}")
                else:
                    updated_machines.append(machine)
            
            if not machine_found:
                logger.warning(f"Machine {delete_id} not found for deletion")
                return dash.no_update, dash.no_update, False
            
            # Update machines data
            machines_data["machines"] = updated_machines
            
            # Auto-save
            try:
                save_success = save_floor_machine_data(floors_data, machines_data)
                if save_success:
                    logger.info(f"Successfully deleted machine {delete_id} and saved layout")
                else:
                    logger.warning(f"Machine {delete_id} deleted but layout save failed")
            except Exception as e:
                logger.error(f"Error saving layout after deleting machine {delete_id}: {e}")
            
            return dash.no_update, machines_data, False
        
        return dash.no_update, dash.no_update, False

    @app.callback(
        Output("floors-data", "data", allow_duplicate=True),
        [Input({"type": "edit-floor-name-btn", "index": ALL}, "n_clicks"),
         Input({"type": "save-floor-name-btn", "index": ALL}, "n_clicks"),
         Input({"type": "cancel-floor-name-btn", "index": ALL}, "n_clicks")],
        [State({"type": "floor-name-input", "index": ALL}, "value"),
         State({"type": "edit-floor-name-btn", "index": ALL}, "id"),
         State({"type": "save-floor-name-btn", "index": ALL}, "id"),
         State({"type": "cancel-floor-name-btn", "index": ALL}, "id"),
         State("floors-data", "data")],
        prevent_initial_call=True
    )
    def handle_floor_name_editing(edit_clicks, save_clicks, cancel_clicks, input_values, 
                                 edit_ids, save_ids, cancel_ids, floors_data):  
        """Handle floor name editing with auto-save"""
        ctx = callback_context
        if not ctx.triggered:
            return dash.no_update
    
        trigger_prop = ctx.triggered[0]["prop_id"]
        
        # Parse which button was clicked and which floor
        if '"type":"save-floor-name-btn"' in trigger_prop:
            # Find which save button was clicked
            for i, clicks in enumerate(save_clicks or []):
                if clicks and i < len(save_ids):
                    floor_id = save_ids[i]["index"]
                    new_name = input_values[i] if i < len(input_values or []) else None
                    
                    if new_name and new_name.strip():
                        # Update the floor name
                        floors = floors_data.get("floors", [])
                        for floor in floors:
                            if floor["id"] == floor_id:
                                floor["name"] = new_name.strip()
                                floor["editing"] = False
                                break
                        
                        floors_data["floors"] = floors
                        
                        # Auto-save the layout (get machines_data fresh)
                        _, machines_data = load_floor_machine_data()
                        if machines_data is None:
                            machines_data = {"machines": [], "next_machine_id": 1}
                        save_floor_machine_data(floors_data, machines_data)
                        logger.info(f"Floor {floor_id} renamed to '{new_name.strip()}' and saved")
                        
                        return floors_data
                    break
        
        elif '"type":"edit-floor-name-btn"' in trigger_prop:
            # Find which edit button was clicked
            for i, clicks in enumerate(edit_clicks or []):
                if clicks and i < len(edit_ids):
                    floor_id = edit_ids[i]["index"]
                    
                    # Set editing mode for this floor
                    floors = floors_data.get("floors", [])
                    for floor in floors:
                        if floor["id"] == floor_id:
                            floor["editing"] = True
                            break
                    
                    floors_data["floors"] = floors
                    return floors_data
                    break
        
        elif '"type":"cancel-floor-name-btn"' in trigger_prop:
            # Find which cancel button was clicked
            for i, clicks in enumerate(cancel_clicks or []):
                if clicks and i < len(cancel_ids):
                    floor_id = cancel_ids[i]["index"]
                    
                    # Cancel editing mode for this floor
                    floors = floors_data.get("floors", [])
                    for floor in floors:
                        if floor["id"] == floor_id:
                            floor["editing"] = False
                            break
                    
                    floors_data["floors"] = floors
                    return floors_data
                    break
        
        return dash.no_update

    @app.callback(
        Output("floors-data", "data", allow_duplicate=True),
        [Input("add-floor-btn", "n_clicks")],
        [State("floors-data", "data"),
         State("machines-data", "data")],
        prevent_initial_call=True
    )
    def add_new_floor_with_save(n_clicks, floors_data, machines_data):
        """Add a new floor with auto-save"""
        if not n_clicks:
            return dash.no_update
        
        floors = floors_data.get("floors", [])
        next_floor_number = len(floors) + 1
        
        # Ordinal suffixes
        def get_ordinal_suffix(n):
            if 10 <= n % 100 <= 20:
                suffix = 'th'
            else:
                suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
            return f"{n}{suffix}"
        
        new_floor = {
            "id": next_floor_number,
            "name": f"{get_ordinal_suffix(next_floor_number)} Floor",
            "editing": False
        }
        
        floors.append(new_floor)
        floors_data["floors"] = floors
        
        # Auto-save the layout
        save_floor_machine_data(floors_data, machines_data)
        logger.info(f"Added new floor: {new_floor['name']} and saved layout")
        
        return floors_data  

    @app.callback(
        Output("save-status", "children"),
        [Input("add-floor-btn", "n_clicks"),
         Input({"type": "save-floor-name-btn", "index": ALL}, "n_clicks"),
         Input({"type": "delete-floor-btn", "index": ALL}, "n_clicks")],
        prevent_initial_call=True
    )
    def show_floor_save_status(add_clicks, save_clicks, delete_clicks):
        """Show save status only when floors are actually modified"""
        if add_clicks or any(save_clicks or []) or any(delete_clicks or []):
            current_time = datetime.now().strftime("%H:%M:%S")
            return f"✓ Saved at {current_time}"
        return ""

    @app.callback(
        Output("save-status", "children", allow_duplicate=True),
        [Input("add-machine-btn", "n_clicks"),
         Input({"type": "machine-ip-dropdown", "index": ALL}, "value")],
        prevent_initial_call=True
    )
    def show_machine_save_status(add_single, ip_values):  # Removed add_multiple parameter
        """Show save status only when machines are added or IP changed"""
        ctx = callback_context
        if not ctx.triggered:
            return ""
        
        trigger_id = ctx.triggered[0]["prop_id"]
        
        # Only show save status for actual button clicks or IP changes
        if "add-machine-btn" in trigger_id or "machine-ip-dropdown" in trigger_id:
            current_time = datetime.now().strftime("%H:%M:%S")
            return f"✓ Saved at {current_time}"
        return ""

    @app.callback(
        Output("save-status", "children", allow_duplicate=True),
        [Input("confirm-delete-btn", "n_clicks")],
        prevent_initial_call=True
    )
    def show_delete_save_status(confirm_clicks):
        """Show save status only when items are actually deleted"""
        if confirm_clicks:
            current_time = datetime.now().strftime("%H:%M:%S")
            return f"✓ Saved at {current_time}"
        return ""


    @app.callback(
        Output("machines-data", "data", allow_duplicate=True),
        [Input("add-machine-btn", "n_clicks")],
        [State("machines-data", "data"),
         State("floors-data", "data")],
        prevent_initial_call=True
    )
    def add_new_machine_with_save(n_clicks, machines_data, floors_data):
        """Add a new blank machine to the selected floor with auto-save"""
        if not n_clicks:
            return dash.no_update
        
        machines = machines_data.get("machines", [])
        next_machine_id = get_next_available_machine_id(machines_data)  # Use helper function
        selected_floor_id = floors_data.get("selected_floor", "all")
        if selected_floor_id == "all":
            floors = floors_data.get("floors", [])
            selected_floor_id = floors[0]["id"] if floors else 1
        
        new_machine = {
            "id": next_machine_id,
            "floor_id": selected_floor_id,
            "name": f"{tr('machine_label', load_language_preference())} {next_machine_id}",
            "ip": None,
            "serial": "Unknown",
            "status": "Offline",
            "model": "Unknown",
            "last_update": "Never"
        }
        
        machines.append(new_machine)
        machines_data["machines"] = machines
        # Remove the next_machine_id update since we're using the helper function
        
        # Auto-save the layout
        save_floor_machine_data(floors_data, machines_data)
        logger.info(f"Added new machine {next_machine_id} to floor {selected_floor_id} and saved layout")
        
        return machines_data

    @app.callback(
        Output("floors-data", "data", allow_duplicate=True),
        [Input({"type": "floor-tile", "index": ALL}, "n_clicks")],
        [State("floors-data", "data")],
        prevent_initial_call=True
    )
    def handle_floor_selection_dynamic(n_clicks_list, floors_data):
        """Handle floor tile selection dynamically"""
        ctx = callback_context
        if not ctx.triggered or not any(n_clicks_list):
            return dash.no_update
        
        # Find which floor was clicked
        triggered_prop = ctx.triggered[0]["prop_id"]
        
        # Extract floor ID from the triggered property
        if "floor-tile" in triggered_prop:
            import json
            import re
            
            # Extract the JSON part before .n_clicks
            json_match = re.search(r'\{[^}]+\}', triggered_prop)
            if json_match:
                try:
                    button_id = json.loads(json_match.group())
                    selected_floor_id = button_id["index"]
                    
                    # Update the selected floor
                    floors_data["selected_floor"] = selected_floor_id
                    return floors_data
                except (json.JSONDecodeError, KeyError):
                    pass
        
        return dash.no_update

    @app.callback(
        Output("machines-data", "data", allow_duplicate=True),
        [Input({"type": "machine-ip-dropdown", "index": ALL}, "value")],
        [State("machines-data", "data"),
         State("floors-data", "data"),
         State({"type": "machine-ip-dropdown", "index": ALL}, "id")],
        prevent_initial_call=True
    )
    def update_machine_selected_ip_with_save(ip_values, machines_data, floors_data, dropdown_ids):
        """Update the selected IP for each machine when dropdown changes with auto-save"""
        if not ip_values or not dropdown_ids:
            return dash.no_update
        
        machines = machines_data.get("machines", [])
        changes_made = False
        
        # Update selected IP for each machine
        for i, ip_value in enumerate(ip_values):
            if i < len(dropdown_ids) and ip_value:
                machine_id = dropdown_ids[i]["index"]
                
                # Find and update the machine
                for machine in machines:
                    if machine["id"] == machine_id:
                        if machine.get("selected_ip") != ip_value:
                            machine["selected_ip"] = ip_value
                            changes_made = True
                            logger.info(f"Updated machine {machine_id} IP selection to {ip_value}")
                        break
        
        if changes_made:
            machines_data["machines"] = machines
            
            # Auto-save the layout
            save_floor_machine_data(floors_data, machines_data)
            logger.info("Machine IP selections saved")
            
            return machines_data
        
        return dash.no_update

    @app.callback(
        [
            Output("section-1-1", "children"),
            Output("production-data-store", "data"),
        ],
    
    
        [
            Input("status-update-interval", "n_intervals"),
            Input("current-dashboard", "data"),
            Input("historical-time-index", "data"),
            Input("historical-data-cache", "data"),
            Input("language-preference-store", "data"),
        ],
        [
            State("app-state", "data"),
            State("app-mode", "data"),
            State("production-data-store", "data"),
            State("weight-preference-store", "data"),
        ],
    
    
        prevent_initial_call=True
    )
    
    
    
    def update_section_1_1(n, which, state_data, historical_data, lang, app_state_data, app_mode, production_data, weight_pref):
    
        """Update section 1-1 with capacity information and update shared production data"""
    
        # only run when we’re in the “main” dashboard
        if which != "main":
            #print("DEBUG: Preventing update for section-1-1")
            raise PreventUpdate


        global previous_counter_values

        #logger.debug(
        #    "update_section_1_1: mode=%s, active_machine=%s, thread_alive=%s, stop_flag=%s",
        #    current_app_mode,
        #    active_machine_id,
        #    app_state.update_thread.is_alive() if app_state.update_thread else False,
        #    app_state.thread_stop_flag,
        #)

        total_capacity_formatted = None
        capacity_count = accepts_count = reject_count = None
        
    
        # Tag definitions - Easy to update when actual tag names are available
        CAPACITY_TAG = "Status.ColorSort.Sort1.Throughput.KgPerHour.Current"
        ACCEPTS_TAG = "Status.Production.Accepts"  # Not used in live mode calculation
        REJECTS_TAG = "Status.ColorSort.Sort1.Total.Percentage.Current"
        OPM_TAG = "Status.ColorSort.Sort1.Throughput.ObjectPerMin.Current"
    
        # Determine if we're in Live or Demo mode
        mode = "demo"  # Default to demo mode
        if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
            mode = app_mode["mode"]
    
        # Only update values if:
        # 1. We're in demo mode (always update with new random values)
        # 2. We're in live mode and connected (update from tags)
        if mode == "live" and app_state_data.get("connected", False):
            # Live mode: get values from OPC UA tags
            total_capacity = 0
    
            # Get total capacity first
            if CAPACITY_TAG in app_state.tags:
                capacity_value = app_state.tags[CAPACITY_TAG]["data"].latest_value
                if capacity_value is not None:
                    total_capacity = convert_capacity_from_kg(capacity_value, weight_pref)
                else:
                    total_capacity = 0
    
            # Rejects come from section 5-2 counter totals and OPM reading
            reject_count = sum(previous_counter_values) if previous_counter_values else 0

            opm = 0
            if OPM_TAG in app_state.tags:
                opm_val = app_state.tags[OPM_TAG]["data"].latest_value
                if opm_val is not None:
                    opm = opm_val

            reject_pct = (reject_count / opm) if opm else 0
            rejects = total_capacity * reject_pct
    
            # Calculate accepts as total_capacity minus rejects
            accepts = total_capacity - rejects
            
            # Ensure accepts doesn't go negative (safety check)
            if accepts < 0:
                accepts = 0
            
            # Update the shared data store
            production_data = {
                "capacity": total_capacity,
                "accepts": accepts,
                "rejects": rejects
            }
            
    
    
        elif mode == "historical":
            hours = state_data.get("hours", 24) if isinstance(state_data, dict) else 24
            hist = (
                historical_data if isinstance(historical_data, dict) and "capacity" in historical_data
                else get_historical_data(timeframe=f"{hours}h")
            )
            cap_vals = hist.get("capacity", {}).get("values", [])
            acc_vals = hist.get("accepts", {}).get("values", [])
            rej_vals = hist.get("rejects", {}).get("values", [])
    
            total_capacity_lbs = sum(cap_vals) / len(cap_vals) if cap_vals else 0
            total_capacity = convert_capacity_from_lbs(total_capacity_lbs, weight_pref)
    
            reject_count = sum(previous_counter_values) if previous_counter_values else 0
            rejects = convert_capacity_from_kg(reject_count * 46, weight_pref)
    
            accepts = total_capacity - rejects
            if accepts < 0:
                accepts = 0
    
            production_data = {
                "capacity": total_capacity,
                "accepts": accepts,
                "rejects": rejects,
            }

        elif mode == "lab":
            mid = active_machine_id
            capacity_count = accepts_count = reject_count = 0

            machine_dir = os.path.join(hourly_data_saving.EXPORT_DIR, str(mid))
            path = _get_latest_lab_file(machine_dir)
            if path:
                stat = os.stat(path)
                mtime = stat.st_mtime
                size = stat.st_size

            else:
                mtime = size = 0

            cache_entry = _lab_production_cache.get(mid)
            if (
                cache_entry is not None
                and cache_entry.get("mtime") == mtime
                and cache_entry.get("size") == size
            ):
                production_data = cache_entry["production_data"]
                total_capacity = production_data["capacity"]
                accepts = production_data["accepts"]
                rejects = production_data["rejects"]
                capacity_count = cache_entry.get("capacity_count", 0)
                accepts_count = cache_entry.get("accepts_count", 0)
                reject_count = cache_entry.get("reject_count", 0)
            else:
                metrics = (
                    load_lab_totals_metrics(mid, active_counters=get_active_counter_flags(mid))
                    if path
                    else None
                )
                if metrics:
                    tot_cap_lbs, acc_lbs, rej_lbs, _ = metrics

                    load_lab_totals(
                        mid, active_counters=get_active_counter_flags(mid)
                    )

                    counter_rates = load_last_lab_counters(mid)
                    reject_count = sum(counter_rates) * 60
                    capacity_count = load_last_lab_objects(mid) * 60

                    accepts_count = max(0, capacity_count - reject_count)

                    total_capacity = convert_capacity_from_lbs(tot_cap_lbs, weight_pref)
                    accepts = convert_capacity_from_lbs(acc_lbs, weight_pref)
                    rejects = convert_capacity_from_lbs(rej_lbs, weight_pref)

                    production_data = {
                        "capacity": total_capacity,
                        "accepts": accepts,
                        "rejects": rejects,
                    }
                else:
                    # No existing lab log yet. Use zeroed placeholders so the
                    # dashboard doesn't display stale live values when switching
                    # to lab mode.
                    total_capacity = accepts = rejects = 0
                    capacity_count = accepts_count = reject_count = 0
                    production_data = {"capacity": 0, "accepts": 0, "rejects": 0}

                _lab_production_cache[mid] = {
                    "mtime": mtime,
                    "size": size,
                    "production_data": production_data,
                    "capacity_count": capacity_count,
                    "accepts_count": accepts_count,
                    "reject_count": reject_count,
                }

        elif mode == "demo":
    
            # Demo mode: generate realistic random capacity value
            demo_lbs = random.uniform(47000, 53000)
            total_capacity = convert_capacity_from_kg(demo_lbs / 2.205, weight_pref)
    
            # Rejects come from section 5-2 counter totals
            reject_count = sum(previous_counter_values) if previous_counter_values else 0
            rejects = convert_capacity_from_kg(reject_count * 46, weight_pref)
    
            # Calculate accepts as the difference
            accepts = total_capacity - rejects
    
            # Update the shared data store
            production_data = {
                "capacity": total_capacity,
                "accepts": accepts,
                "rejects": rejects
            }
        else:
            # If not live+connected or demo, use existing values from the store
            total_capacity = production_data.get("capacity", 50000)
            accepts = production_data.get("accepts", 47500)
            rejects = production_data.get("rejects", 2500)
        
        # Calculate percentages
        total = accepts + rejects
        accepts_percent = (accepts / total * 100) if total > 0 else 0
        rejects_percent = (rejects / total * 100) if total > 0 else 0
        
        # Format values with commas for thousands separator and limited decimal places
        if total_capacity_formatted is None:
            total_capacity_formatted = f"{total_capacity:,.0f}"
        accepts_formatted = f"{accepts:,.2f}"
        rejects_formatted = f"{rejects:,.2f}"
        accepts_percent_formatted = f"{accepts_percent:.2f}"
        rejects_percent_formatted = f"{rejects_percent:.2f}"

        capacity_count_fmt = (
            f"{capacity_count:,.0f}" if capacity_count is not None else None
        )
        accepts_count_fmt = (
            f"{accepts_count:,.0f}" if accepts_count is not None else None
        )
        reject_count_fmt = (
            f"{reject_count:,.0f}" if reject_count is not None and mode != "live" else None
        )

        cap_display = (
            f"{capacity_count_fmt} pcs / {total_capacity_formatted} {capacity_unit_label(weight_pref)}"
            if capacity_count_fmt is not None
            else f"{total_capacity_formatted} {capacity_unit_label(weight_pref)}"
        )
        acc_display = (
            f"{accepts_count_fmt} pcs / {accepts_formatted} {capacity_unit_label(weight_pref, False)} "
            if accepts_count_fmt is not None
            else f"{accepts_formatted} {capacity_unit_label(weight_pref, False)} "
        )
        rej_display = (
            f"{reject_count_fmt} pcs / {rejects_formatted} {capacity_unit_label(weight_pref, False)} "
            if reject_count_fmt is not None
            else f"{rejects_formatted} {capacity_unit_label(weight_pref, False)} "
        )
        
        # Define styles for text
    
        base_style = {"fontSize": "1.6rem", "lineHeight": "1.6rem", "fontFamily": NUMERIC_FONT}
        label_style = {"fontWeight": "bold", "fontSize": "1.6rem"}
        incoming_style = {"color": "blue", "fontSize": "2.4rem", "fontFamily": NUMERIC_FONT}
        accepts_style = {"color": "green", "fontSize": "1.8rem", "fontFamily": NUMERIC_FONT}
        rejects_style = {"color": "red", "fontSize": "1.8rem", "fontFamily": NUMERIC_FONT}
    
        
        # Create the section content
        section_content = html.Div([
            # Title with mode indicator
            dbc.Row([
                dbc.Col(html.H6(tr("production_capacity_title", lang), className="text-left mb-2"), width=8),
                dbc.Col(
                    dbc.Button(
                        tr("update_counts_title", lang),
                        id="open-update-counts",
                        color="primary",
                        size="sm",
                        className="float-end"
                    ),
                    width=4
                )
            ]),
            
            # Capacity data
            html.Div([
                html.Span(tr("capacity", lang) + ": ", style=label_style),
                html.Br(),
                html.Span(
                    cap_display,
                    style={**incoming_style, "marginLeft": "20px"},
                ),
            ], className="mb-2", style=base_style),
            
            html.Div([
                html.Span(tr("accepts", lang) + ": ", style=label_style),
                html.Br(),
                html.Span(
                    acc_display,
                    style={**accepts_style,"marginLeft":"20px"},
                ),
                html.Span(f"({accepts_percent_formatted}%)", style=accepts_style),
            ], className="mb-2", style=base_style),
            
            html.Div([
                html.Span(tr("rejects", lang) + ": ", style=label_style),
                html.Br(),
                html.Span(
                    rej_display,
                    style={**rejects_style,"marginLeft":"20px"},
                ),
                html.Span(f"({rejects_percent_formatted}%)", style=rejects_style),
            ], className="mb-2", style=base_style),
        ], className="p-1")
        
        return section_content, production_data

    @app.callback(
        Output("update-counts-modal-body", "children"),
        [Input("status-update-interval", "n_intervals"),
         Input("current-dashboard",       "data"),
         Input("opc-pause-state", "data"),
         Input("language-preference-store", "data")],
        [State("app-state", "data"),
         State("app-mode", "data"),
         State("user-inputs", "data")],
        prevent_initial_call=True
    )
    def update_section_1_1b_with_manual_pause(n, which, pause_state, lang, app_state_data, app_mode, user_inputs):
        """Update section 1-1b with manual pause/resume system"""
        # only run when we’re in the “main” dashboard
        if which != "main":
            raise PreventUpdate
        
        # Tag definitions for live mode
        WEIGHT_TAG = "Settings.ColorSort.TestWeightValue"
        COUNT_TAG = "Settings.ColorSort.TestWeightCount"
        UNITS_TAG = "Status.Production.Units"
        
        # Default values
        default_weight = 500.0
        default_count = 1000
        default_unit = "lb"
        
        # Determine if we're in Live or Demo mode
        mode = "demo"
        if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
            mode = app_mode["mode"]
        
        # Check if OPC reading is paused
        is_paused = pause_state.get("paused", False)
        
        # Initialize values
        weight_value = default_weight
        count_value = default_count
        unit_value = default_unit
        opc_weight = None
        opc_count = None
        reading_status = "N/A"
        
        if mode in LIVE_LIKE_MODES and app_state_data.get("connected", False):
            # Always read the current OPC values for display in the status line
            if WEIGHT_TAG in app_state.tags:
                tag_value = app_state.tags[WEIGHT_TAG]["data"].latest_value
                if tag_value is not None:
                    opc_weight = float(tag_value)
                    
            if COUNT_TAG in app_state.tags:
                tag_value = app_state.tags[COUNT_TAG]["data"].latest_value
                if tag_value is not None:
                    opc_count = int(tag_value)
                    
            if UNITS_TAG in app_state.tags:
                tag_value = app_state.tags[UNITS_TAG]["data"].latest_value
                if tag_value is not None:
                    unit_value = tag_value
            
            # Decide what values to use based on pause state
            if is_paused:
                # OPC reading is paused - use user inputs if available, otherwise use last known OPC values
                if user_inputs:
                    weight_value = user_inputs.get("weight", opc_weight or default_weight)
                    count_value = user_inputs.get("count", opc_count or default_count)
                    unit_value = user_inputs.get("units", unit_value)
                else:
                    # No user inputs yet, use current OPC values as starting point
                    weight_value = opc_weight if opc_weight is not None else default_weight
                    count_value = opc_count if opc_count is not None else default_count
                reading_status = "⏸ Paused (Manual)"
            else:
                # OPC reading is active - always use current OPC values
                weight_value = opc_weight if opc_weight is not None else default_weight
                count_value = opc_count if opc_count is not None else default_count
                reading_status = "▶ Reading from OPC"
                
            logger.info(f"Live mode: Paused={is_paused} | OPC W={opc_weight}, C={opc_count} | Using W={weight_value}, C={count_value}")
        else:
            # Demo mode or not connected - use user inputs or defaults
            if user_inputs:
                weight_value = user_inputs.get("weight", default_weight)
                count_value = user_inputs.get("count", default_count)
                unit_value = user_inputs.get("units", default_unit)
            reading_status = "Demo mode" if mode == "demo" else "Not connected"
        
        return html.Div([
            # Title
            html.H6(tr("update_counts_title", lang), className="mb-0 text-center small"),
            
            # Show current OPC values and reading status in live mode
            html.Div([
                #html.Small(
                #    f"OPC: W={opc_weight if opc_weight is not None else 'N/A'}, "
                #    f"C={opc_count if opc_count is not None else 'N/A'} | "
                #    f"Status: {reading_status}", 
                #    className="text-info"
                #)
            ], className="mb-1 text-center") if mode in LIVE_LIKE_MODES and app_state_data.get("connected", False) else html.Div(),
            
            # Controls container 
            html.Div([
                # Units row
                dbc.Row([
                    dbc.Col(
                        html.Label(tr("units_label", lang), className="fw-bold pt-0 text-end small"),
                        width=3,
                    ),
                    dbc.Col(
                        dcc.Dropdown(
                            id="unit-selector",
                            options=[
                                {"label": "oz", "value": "oz"},
                                {"label": "lb", "value": "lb"},
                                {"label": "g", "value": "g"},
                                {"label": "kg", "value": "kg"}
                            ],
                            value=unit_value,
                            clearable=False,
                            style={"width": "100%", "fontSize": "0.8rem"}
                        ),
                        width=9,
                    ),
                ], className="mb-1"),
                
                # Weight row
                dbc.Row([
                    dbc.Col(
                        html.Label(tr("weight_label", lang), className="fw-bold pt-0 text-end small"),
                        width=3,
                    ),
                    dbc.Col(
                        dbc.Input(
                            id="weight-input",
                            type="number",
                            min=0,
                            step=1,
                            value=weight_value,
                            style={"width": "100%", "height": "1.4rem"}
                        ),
                        width=9,
                    ),
                ]),
    
                # Count row
                dbc.Row([
                    dbc.Col(
                        html.Label(tr("count_label", lang), className="fw-bold pt-0 text-end small"),
                        width=3,
                    ),
                    dbc.Col(
                        dbc.Input(
                            id="count-input",
                            type="number",
                            min=0,
                            step=1,
                            value=count_value,
                            style={"width": "100%", "height": "1.4rem"}
                        ),
                        width=9,
                    ),
                ], className="mb-1"),
                
                # Two button row - same width, half each
                dbc.Row([
                    dbc.Col(width=3),  # Empty space to align with inputs
                    dbc.Col([
                        dbc.ButtonGroup([
                            dbc.Button(
                                tr("pause_opc_read_button", lang) if not is_paused else tr("resume_opc_read_button", lang),
                                id="toggle-opc-pause",
                                color="warning" if not is_paused else "success",
                                size="sm",
                                style={"fontSize": "0.65rem", "padding": "0.2rem 0.3rem"}
                            ),
                            dbc.Button(
                                tr("save_to_opc_button", lang),
                                id="save-count-settings",
                                color="primary",
                                size="sm",
                                style={"fontSize": "0.65rem", "padding": "0.2rem 0.3rem"}
                            )
                        ], className="w-100")
                    ], width=9)
                ]),
                
                # Notification area
                dbc.Row([
                    dbc.Col(width=3),
                    dbc.Col(
                        html.Div(id="save-counts-notification", className="small text-success mt-1"),
                        width=9
                    )
                ])
            ]),
        ], className="p-1 ps-2")

    @app.callback(
        Output("opc-pause-state", "data"),
        [Input("toggle-opc-pause", "n_clicks")],
        [State("opc-pause-state", "data"),
         State("app-mode", "data")],
        prevent_initial_call=True
    )
    def toggle_opc_pause(n_clicks, current_pause_state, app_mode):
        """Toggle OPC reading pause state"""
        if not n_clicks:
            return dash.no_update
        
        # Only allow pausing in live mode
        mode = "demo"
        if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
            mode = app_mode["mode"]
        
        if mode not in LIVE_LIKE_MODES:
            return dash.no_update
        
        # Toggle the pause state
        current_paused = current_pause_state.get("paused", False)
        new_paused = not current_paused
        
        logger.info(f"OPC reading {'paused' if new_paused else 'resumed'} by user")
        
        return {"paused": new_paused}

    @app.callback(
        Output("user-inputs", "data", allow_duplicate=True),
        [Input("mode-selector", "value")],
        [State("user-inputs", "data")],
        prevent_initial_call=True
    )
    def clear_inputs_on_mode_switch(mode, current_inputs):
        """Clear user inputs when switching to live mode"""
        if mode in LIVE_LIKE_MODES:
            logger.info("Switched to live mode - clearing user inputs")
            return {}  # Clear all user inputs
        return dash.no_update

    @app.callback(
        [Output("save-counts-notification", "children"),
         Output("opc-pause-state", "data", allow_duplicate=True)],
        [Input("save-count-settings", "n_clicks")],
        [State("weight-input", "value"),
         State("count-input", "value"),
         State("unit-selector", "value"),
         State("app-state", "data"),
         State("app-mode", "data"),
         State("opc-pause-state", "data")],
        prevent_initial_call=True
    )
    def save_and_resume_opc_reading(n_clicks, weight_value, count_value, unit_value, 
                                   app_state_data, app_mode, pause_state):
        """Save the count settings to OPC UA tags and resume OPC reading"""
        if not n_clicks:
            return dash.no_update, dash.no_update
        
        # Tag definitions for writing
        WEIGHT_TAG = "Settings.ColorSort.TestWeightValue"
        COUNT_TAG = "Settings.ColorSort.TestWeightCount"
        
        # Determine if we're in Live mode
        mode = "demo"
        if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
            mode = app_mode["mode"]
        
        # Only write to tags in live mode when connected
        if mode in LIVE_LIKE_MODES and app_state_data.get("connected", False):
            try:
                success_messages = []
                error_messages = []
                
                # Write weight value to OPC UA tag
                if WEIGHT_TAG in app_state.tags and weight_value is not None:
                    try:
                        app_state.tags[WEIGHT_TAG]["node"].set_value(float(weight_value))
                        success_messages.append(f"Weight: {weight_value}")
                        logger.info(f"Successfully wrote weight value {weight_value} to {WEIGHT_TAG}")
                    except Exception as e:
                        error_messages.append(f"Weight write error: {str(e)}")
                        logger.error(f"Error writing weight value to {WEIGHT_TAG}: {e}")
                
                # Write count value to OPC UA tag
                if COUNT_TAG in app_state.tags and count_value is not None:
                    try:
                        app_state.tags[COUNT_TAG]["node"].set_value(int(count_value))
                        success_messages.append(f"Count: {count_value}")
                        logger.info(f"Successfully wrote count value {count_value} to {COUNT_TAG}")
                    except Exception as e:
                        error_messages.append(f"Count write error: {str(e)}")
                        logger.error(f"Error writing count value to {COUNT_TAG}: {e}")
                
                # Prepare notification message and resume reading if successful
                if success_messages and not error_messages:
                    notification = f"✓ Saved: {', '.join(success_messages)} - OPC reading resumed"
                    # Resume OPC reading after successful save
                    resumed_state = {"paused": False}
                    logger.info("OPC reading resumed after successful save")
                    return notification, resumed_state
                elif success_messages and error_messages:
                    notification = f"⚠ Partial: {', '.join(success_messages)}. Errors: {', '.join(error_messages)}"
                    return notification, dash.no_update
                elif error_messages:
                    notification = f"✗ Errors: {', '.join(error_messages)}"
                    return notification, dash.no_update
                else:
                    notification = "⚠ No OPC UA tags found for writing"
                    return notification, dash.no_update
                
            except Exception as e:
                error_msg = f"✗ Save failed: {str(e)}"
                logger.error(f"Unexpected error saving count settings: {e}")
                return error_msg, dash.no_update
        
        else:
            # Not in live mode or not connected
            if mode == "demo":
                return "✓ Saved locally (Demo mode)", dash.no_update
            else:
                return "⚠ Not connected to OPC server", dash.no_update

    @app.callback(
        Output("user-inputs", "data"),
        [
            Input("unit-selector", "value"),
            Input("weight-input", "value"),
            Input("count-input", "value")
        ],
        [State("user-inputs", "data"),
         State("app-mode", "data")],
        prevent_initial_call=True
    )
    def save_user_inputs_with_mode_tracking(units, weight, count, current_data, app_mode):
        """Save user input values when they change (with mode tracking)"""
        ctx = callback_context
        if not ctx.triggered:
            return current_data or {"units": "lb", "weight": 500.0, "count": 1000}
        
        # Get which input triggered the callback
        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        
        # Determine current mode
        mode = "demo"
        if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
            mode = app_mode["mode"]
        
        # Create a new data object with defaults if current_data is None
        new_data = current_data.copy() if current_data else {"units": "lb", "weight": 500.0, "count": 1000}
        
        # Mark if user made changes in live mode
        if mode in LIVE_LIKE_MODES:
            new_data["live_mode_user_changed"] = True
            #logger.debug("User changed %s in live mode", trigger_id)
        
        # Update the value that changed
        if trigger_id == "unit-selector" and units is not None:
            new_data["units"] = units
        elif trigger_id == "weight-input" and weight is not None:
            new_data["weight"] = weight
        elif trigger_id == "count-input" and count is not None:
            new_data["count"] = count
        
        return new_data

    @app.callback(
        Output("section-1-2", "children"),
        [Input("production-data-store", "data"),
         Input("status-update-interval", "n_intervals"),
         Input("current-dashboard", "data"),
         Input("historical-time-index", "data"),
         Input("historical-data-cache", "data"),
         Input("counter-view-mode", "data")],
        [State("app-state", "data"),
         State("app-mode", "data")],
        prevent_initial_call=True
    )
    def update_section_1_2(production_data, n_intervals, which, state_data, historical_data, counter_mode, app_state_data, app_mode):

        """Update section 1-2 with side-by-side pie charts for accepts/rejects and reject breakdown.

        The first pie chart normally uses accepts/rejects from the production data. When the
        counter display mode is set to "percent" (via the section 5-2 threshold settings) the
        reject percentage is instead derived from the sum of all 12 counter percentages."""
        
        # Only run when we're in the "main" dashboard
        if which != "main":
            raise PreventUpdate
            
        global previous_counter_values
        
        counter_colors = {
            1: "green",       # Blue
            2: "lightgreen",      # Green
            3: "orange",     # Orange
            4: "blue",      # Black
            5: "#f9d70b",    # Yellow (using hex to ensure visibility)
            6: "magenta",    # Magenta
            7: "cyan",       # Cyan
            8: "red",        # Red
            9: "purple",
            10: "brown",
            11: "gray",
            12: "lightblue"
        }
    
        # Extract data from the shared production data store
        total_capacity = production_data.get("capacity", 50000)
        accepts = production_data.get("accepts", 47500)
        rejects = production_data.get("rejects", 2500)

        # Determine accepts/rejects percentages for the first pie chart
        # Percent view only affects display in section 5-2, not how rejects are
        # calculated here. Always derive the percentages from the production
        # data counts so switching display modes does not alter totals.
        total = accepts + rejects
        accepts_percent = (accepts / total * 100) if total > 0 else 0
        rejects_percent = (rejects / total * 100) if total > 0 else 0
        
        # Second chart data - Use the counter values for the reject breakdown
        # Ensure previous_counter_values has a predictable baseline
        if 'previous_counter_values' not in globals() or not previous_counter_values:
            # Start counters at zero instead of random demo values
            previous_counter_values = [0] * 12
        
        # Calculate the total of all counter values
        total_counter_value = sum(previous_counter_values)
        
        if total_counter_value > 0:
            # Create percentage breakdown for each counter relative to total rejects
            # Filter out counters with zero values and track their original counter numbers
            reject_counters = {}
            counter_indices = {}  # Track which counter number each entry corresponds to
            for i, value in enumerate(previous_counter_values):
                if value > 0:  # Only include counters with values greater than 0
                    counter_name = f"Counter {i+1}"
                    counter_number = i + 1  # Store the actual counter number
                    # This counter's percentage of the total rejects
                    counter_percent_of_rejects = (value / total_counter_value) * 100
                    reject_counters[counter_name] = counter_percent_of_rejects
                    counter_indices[counter_name] = counter_number
        else:
            # Fallback if counter values sum to zero - create empty dict
            reject_counters = {}
        
        # Create first pie chart - Accepts/Rejects ratio
        fig1 = go.Figure(data=[go.Pie(
            labels=['Accepts', 'Rejects'],
            values=[accepts_percent, rejects_percent],  # Use the exact percentages from section 1-1
            hole=.4,
            marker_colors=['green', 'red'],
            textinfo='percent',
            insidetextorientation='radial',
            rotation = 90
        )])
    
        # Update layout for first chart with centered title
        fig1.update_layout(
            title={
                'text': "Accept/Reject Ratio",
                'y': 0.99,
                'x': 0.5,
                'xanchor': 'center',
                'yanchor': 'top'
            },
            margin=dict(l=10, r=10, t=25, b=10),
            height=210,
            showlegend=False,  # Set showlegend to False to remove the legend
            plot_bgcolor='var(--chart-bg)',
            paper_bgcolor='var(--chart-bg)'
        )
    
        # Create second pie chart - Reject breakdown (only if we have non-zero data)
        if reject_counters:  # Only create chart if we have data
            # Extract data for the second pie chart
            labels = list(reject_counters.keys())
            values = list(reject_counters.values())
            # Use the correct counter numbers for colors instead of sequential indices
            colors = [counter_colors.get(counter_indices[label], "gray") for label in labels]
    
            # Create second pie chart - Reject breakdown
            fig2 = go.Figure(data=[go.Pie(
                labels=labels,
                values=values,
                hole=.4,
                marker_colors=colors,
                textinfo='percent',
                insidetextorientation='radial'
            )])
    
            # Update layout for second chart with centered title
            fig2.update_layout(
                title={
                    'text': "Reject Percentages",
                    'y': 0.99,
                    'x': 0.5,
                    'xanchor': 'center',
                    'yanchor': 'top'
                },
                margin=dict(l=10, r=10, t=25, b=10),
                height=210,
                showlegend=False,  # Set showlegend to False to remove the legend
                plot_bgcolor='var(--chart-bg)',
                paper_bgcolor='var(--chart-bg)'
            )
            
            # Second chart content
            second_chart_content = dcc.Graph(
                figure=fig2,
                config={'displayModeBar': False, 'responsive': True},
                style={'width': '100%', 'height': '100%'}
            )
        else:
            # No data available - show placeholder
            second_chart_content = html.Div([
                html.Div("No Reject Data", className="text-center text-muted d-flex align-items-center justify-content-center h-100"),
            ], style={'minHeight': '200px', 'height': 'auto', 'border': '1px solid #dee2e6', 'borderRadius': '0.25rem'})
        
        # Return the layout with both charts side by side
        return html.Div([
            dbc.Row([
                # First chart
                dbc.Col(
                    dcc.Graph(
                        figure=fig1,
                        config={'displayModeBar': False, 'responsive': True},
                        style={'width': '100%', 'height': '100%'}
                    ),
                    width=6
                ),
                
                # Second chart or placeholder
                dbc.Col(
                    second_chart_content,
                    width=6
                ),
            ]),
        ])

    @app.callback(
        Output("user-inputs", "data", allow_duplicate=True),
        [Input("auto-connect-trigger", "data")],
        [State("user-inputs", "data")],
        prevent_initial_call=True
    )
    def initialize_user_inputs(trigger, current_data):
        """Initialize user inputs on page load if not already set"""
        if current_data:
            return dash.no_update
        return {"units": "lb", "weight": 500.0, "count": 1000}

    @app.callback(
        Output("section-2", "children"),
        [Input("status-update-interval", "n_intervals"),
         Input("current-dashboard",       "data"),
         Input("language-preference-store", "data")],
        [State("app-state", "data"),
         State("app-mode", "data")],
        prevent_initial_call=True
    )
    def update_section_2(n_intervals, which, lang, app_state_data, app_mode):
        """Update section 2 with three status boxes and feeder gauges"""
        
          # only run when we’re in the “main” dashboard
        if which != "main":
            raise PreventUpdate
        # CRITICAL: Check if we actually have a connected machine and valid app_state
        if not app_state_data.get("connected", False):
            #logger.debug("No connected machine - preventing section update")
            raise PreventUpdate
        
        if not app_state.client or not app_state.tags:
            #logger.debug("No valid client or tags - preventing section update")
            raise PreventUpdate
            # or return [no_update, no_update]
        # Tag definitions
        PRESET_NUMBER_TAG = "Status.Info.PresetNumber"
        PRESET_NAME_TAG = "Status.Info.PresetName"
        GLOBAL_FAULT_TAG = "Status.Faults.GlobalFault"
        GLOBAL_WARNING_TAG = "Status.Faults.GlobalWarning"
        FEEDER_TAG_PREFIX = "Status.Feeders."
        FEEDER_TAG_SUFFIX = "IsRunning"
        MODEL_TAG = "Status.Info.Type"  # Added this tag to check model type
        
        # Determine if we're in Live or Demo mode
        mode = "demo"  # Default to demo mode
        if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
            mode = app_mode["mode"]
        #logger.debug("Section 2: mode=%s, connected=%s", mode, app_state_data.get("connected", False))
        
        # Define color styles for different states
        success_style = {"backgroundColor": "#28a745", "color": "white"}  # Green
        danger_style = {"backgroundColor": "#dc3545", "color": "white"}   # Red
        warning_style = {"backgroundColor": "#ffc107", "color": "black"}  # Yellow
        secondary_style = {"backgroundColor": "#6c757d", "color": "white"}  # Gray
        
        # Check model type to determine number of gauges to show
        show_all_gauges = True  # Default to showing all 4 gauges
        model_type = None
        
        # Define box styles based on mode
        if mode == "demo":
            # Demo mode - force green for all boxes
            preset_text = "1 Yellow CORN"
            preset_style = success_style
            
            status_text = "GOOD"
            status_style = success_style
            
            feeder_text = "Running"
            feeder_style = success_style
            
            # In demo mode, show all gauges
            show_all_gauges = True
            
        elif not app_state_data.get("connected", False):
            # Not connected - all gray
            preset_text = "Unknown"
            preset_style = secondary_style
            
            status_text = "Unknown"
            status_style = secondary_style
            
            feeder_text = "Unknown"
            feeder_style = secondary_style
            
            # When not connected, show all gauges
            show_all_gauges = True
            
        else:
            # Live mode - FIXED to properly access the global app_state
            preset_number = "N/A"
            preset_name = "N/A"
            
            # Check model type first to determine gauge visibility
            if MODEL_TAG in app_state.tags:
                model_type = app_state.tags[MODEL_TAG]["data"].latest_value
                if model_type == "RGB400":
                    show_all_gauges = False  # Hide gauges 3 and 4
                    #logger.info("Model type is RGB400 - hiding gauges 3 and 4")
                else:
                    show_all_gauges = True
                    #logger.info(f"Model type is {model_type} - showing all gauges")
            
            # Try to get preset information - FIXED to use proper app_state reference
            if PRESET_NUMBER_TAG in app_state.tags:
                preset_number = app_state.tags[PRESET_NUMBER_TAG]["data"].latest_value
                if preset_number is None:
                    preset_number = "N/A"
                #logger.info(f"Retrieved preset number: {preset_number}")
                    
            if PRESET_NAME_TAG in app_state.tags:
                preset_name = app_state.tags[PRESET_NAME_TAG]["data"].latest_value
                if preset_name is None:
                    preset_name = "N/A"
                #logger.info(f"Retrieved preset name: {preset_name}")
                    
            preset_text = f"{preset_number} {preset_name}"
            preset_style = success_style  # Default to green
            
            # Check fault and warning status - FIXED to use proper app_state reference
            has_fault = False
            has_warning = False
            
            if GLOBAL_FAULT_TAG in app_state.tags:
                has_fault = bool(app_state.tags[GLOBAL_FAULT_TAG]["data"].latest_value)
                
            if GLOBAL_WARNING_TAG in app_state.tags:
                has_warning = bool(app_state.tags[GLOBAL_WARNING_TAG]["data"].latest_value)
                
            # Set status text and style based on fault/warning
            if has_fault:
                status_text = "FAULT"
                status_style = danger_style
            elif has_warning:
                status_text = "WARNING"
                status_style = warning_style
            else:
                status_text = "GOOD"
                status_style = success_style
    
            if status_text in ("FAULT", "WARNING", "GOOD"):
                status_text = tr(f"{status_text.lower()}_status", lang)
                
            # Check feeder status - FIXED to use proper app_state reference
            feeder_running = False
            
            # Check only the appropriate number of feeders based on model
            max_feeder = 2 if not show_all_gauges else 4
            for feeder_num in range(1, max_feeder + 1):
                tag_name = f"{FEEDER_TAG_PREFIX}{feeder_num}{FEEDER_TAG_SUFFIX}"
                if tag_name in app_state.tags:
                    if bool(app_state.tags[tag_name]["data"].latest_value):
                        feeder_running = True
                        break
                        
            if feeder_running:
                feeder_text = tr("running_state", lang)
                feeder_style = success_style
            else:
                feeder_text = tr("stopped_state", lang)
                feeder_style = secondary_style
            
            # Add debug logging for live mode
            #logger.debug(
            #    "Live mode - Preset: %s , Status: %s, Feeder: %s",
            #    preset_text,
            #    status_text,
            #    feeder_text,
            #)
        
        # Create the feeder rate boxes with conditional display
        feeder_boxes = create_feeder_rate_boxes(app_state_data, app_mode, mode, show_all_gauges)
        
        # Create the three boxes with explicit styling and add feeder gauges
        return html.Div([
            html.H5(tr("machine_status_title", lang), className="mb-2 text-left"),
            
            # Box 1 - Preset - Using inline styling instead of Bootstrap classes
            html.Div([
                html.Div([
                    html.Div([
                        html.Span(tr("preset_label", lang) + " ", className="fw-bold"),
                        html.Span(preset_text),
                    ], className="h7"),
                ], className="p-3"),
            ], className="mb-2", style={"borderRadius": "0.25rem", **preset_style}),
            
            # Box 2 - Status - Using inline styling
            html.Div([
                html.Div([
                    html.Div([
                        html.Span(tr("status_label", lang) + " ", className="fw-bold"),
                        html.Span(status_text),
                    ], className="h7"),
                ], className="p-3"),
            ], className="mb-2", style={"borderRadius": "0.25rem", **status_style}),
            
            # Box 3 - Feeders - Using inline styling
            html.Div([
                html.Div([
                    html.Div([
                        html.Span(tr("feeders_label", lang) + " ", className="fw-bold"),
                        html.Span(feeder_text),
                    ], className="h7"),
                ], className="p-3"),
            ], className="mb-2", style={"borderRadius": "0.25rem", **feeder_style}),
    
            # Row of feeder rate boxes
            feeder_boxes,
        ])

    @app.callback(
        Output("section-3-1", "children"),
        [Input("status-update-interval", "n_intervals"),
         Input("current-dashboard",       "data"),
         Input("language-preference-store", "data")],
        [State("additional-image-store", "data")],
        prevent_initial_call=True
    )
    
    def update_section_3_1(n_intervals, which, lang, additional_image_data):
        """Update section 3-1 with the Load Image button and additional image if loaded"""
        # Debug logging
        #logger.info(f"Image data in section-3-1: {'' if not additional_image_data else 'Data present'}")
        
        # only run when we’re in the “main” dashboard
        if which != "main":
            raise PreventUpdate
            # or return [no_update, no_update]
        # Check if additional image is loaded
        has_additional_image = additional_image_data and 'image' in additional_image_data
        
        # More debug logging
        #if has_additional_image:
        #    logger.info("Section 3-1: Image found in data store")
        #else:
        #    logger.info("Section 3-1: No image in data store")
        
        # Create the additional image section with auto-scaling
        if has_additional_image:
            additional_image_section = html.Div([
                html.Img(
                    src=additional_image_data['image'],
                    style={
                        'width': '100%',
                        'maxWidth': '100%',
                        'maxHeight': '130px',
                        'objectFit': 'contain',
                        'margin': '0 auto',
                        'display': 'block'
                    }
                )
            ], className="text-center", style={'minHeight': '130px', 'height': 'auto', 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center'})
        else:
            additional_image_section = html.Div(
                "No custom image loaded",
                className="text-center text-muted",
                style={'minHeight': '130px', 'height': 'auto', 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center'}
            )
        
        return html.Div([
            # Title and Load button row
            dbc.Row([
                # Title
                dbc.Col(html.H5(tr("corporate_logo_title", lang), className="mb-0"), width=8),
                # Load button
                dbc.Col(
                    dbc.Button(
                        tr("load_image_button", lang),
                        id="load-additional-image",
                        color="primary", 
                        size="sm",
                        className="float-end"
                    ), 
                    width=4
                ),
            ], className="mb-2 align-items-center"),
            
            # Additional image section with fixed height
            additional_image_section,
        ], style={'minHeight': '175px', 'height': 'auto'})  # Flexible height for section 3-1

    @app.callback(
        Output("upload-modal", "is_open"),
        [Input("load-additional-image", "n_clicks"),
         Input("close-upload-modal", "n_clicks")],
        [State("upload-modal", "is_open")],
        prevent_initial_call=True
    )
    def toggle_upload_modal(load_clicks, close_clicks, is_open):
        """Toggle the upload modal when the Load Image button is clicked"""
        ctx = callback_context
        
        # If callback wasn't triggered, don't change the state
        if not ctx.triggered:
            return dash.no_update
            
        # Get the ID of the component that triggered the callback
        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        
        # If the Load Image button was clicked and modal is not already open, open it
        if trigger_id == "load-additional-image" and load_clicks and not is_open:
            return True
        
        # If the Close button was clicked and modal is open, close it
        elif trigger_id == "close-upload-modal" and close_clicks and is_open:
            return False
        
        # Otherwise, don't change the state
        return is_open

    @app.callback(
        Output("section-3-2", "children"),
        [Input("status-update-interval", "n_intervals"),
         Input("current-dashboard",       "data"),
         Input("language-preference-store", "data")],
        [State("app-state", "data"),
         State("app-mode", "data")],
        prevent_initial_call=True
    
    )
    def update_section_3_2(n_intervals, which, lang, app_state_data, app_mode):
        """Update section 3-2 with machine information and Satake logo"""
    
        # only run when we’re in the “main” dashboard
        if which != "main":
            raise PreventUpdate
            # or return [no_update, no_update]
    
        # Tag definitions for easy updating
        SERIAL_TAG = "Status.Info.Serial"
        MODEL_TAG = "Status.Info.Type"  # Added tag for model information
        
        # Determine if we're in Live or Demo mode
        mode = "demo"  # Default to demo mode
        if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
            mode = app_mode["mode"]
        #logger.debug(
        #    "Section 3-2: mode=%s, connected=%s",
        #    mode,
        #    app_state_data.get("connected", False),
        #)

        # Generate current timestamp for "Last Update" display. This must be
        # evaluated on each callback invocation so the UI reflects the actual
        # update time rather than a static value.
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if mode == "demo":
            # Demo mode values
            serial_number = "2025_1_4CH"
            status_text = "DEMO"
            model_text = "Enpresor RGB"
            last_update = current_time
            status_class = "text-success"
        else:
            # Live mode - use original code with model tag
            serial_number = "Unknown"
            if app_state_data.get("connected", False) and SERIAL_TAG in app_state.tags:
                serial_number = app_state.tags[SERIAL_TAG]["data"].latest_value or "Unknown"
            
            # Get the model from the Type tag when in Live mode
            model_text = "ENPRESOR RGB"  # Default model
            if app_state_data.get("connected", False) and MODEL_TAG in app_state.tags:
                model_from_tag = app_state.tags[MODEL_TAG]["data"].latest_value
                if model_from_tag:
                    model_text = model_from_tag  # Use the model from the tag if available
            
            status_text = "Online" if app_state_data.get("connected", False) else "Offline"
            status_class = "text-success" if app_state_data.get("connected", False) else "text-secondary"
            last_update = current_time if app_state_data.get("connected", False) else "Never"
        
        return html.Div([
            # Title
            html.H5(tr("machine_info_title", lang), className="mb-2 text-center"),
            
            # Custom container with fixed height and auto-scaling image
            html.Div([
                # Logo container (left side)
                html.Div([
                    html.Img(
                        src=f'data:image/png;base64,{SATAKE_LOGO}',
                        style={
                            'width': '100%',
                            'maxWidth': '100%',
    
                            'maxHeight': '200px',  # Increased maximum height
    
                            'objectFit': 'contain',
                            'margin': '0 auto',
                            'display': 'block'
                        }
                    )
                ], className="machine-info-logo", style={
                    'flex': '0 0 auto',
    
                    'width': '45%',
                    'maxWidth': '180px',
                    'minHeight': '180px',  # Increased minimum height for logo container
    
                    'display': 'flex',
                    'alignItems': 'center',
                    'justifyContent': 'center',
                    'paddingRight': '15px'
                }),
                
                # Information container (right side)
                html.Div([
                    html.Div([
                        html.Span(tr("serial_number_label", lang) + " ", className="fw-bold"),
                        html.Span(serial_number),
                    ], className="mb-1"),
                    
                    html.Div([
                        html.Span(tr("status_label", lang) + " ", className="fw-bold"),
                        html.Span(status_text, className=status_class),
                    ], className="mb-1"),
                    
                    html.Div([
                        html.Span(tr("model_label", lang) + " ", className="fw-bold"),
                        html.Span(model_text),
                    ], className="mb-1"),
                    
                    html.Div([
                        html.Span(tr("last_update_label", lang) + " ", className="fw-bold"),
                        html.Span(last_update),
                    ], className="mb-1"),
                ], style={
                    'flex': '1',
                    'paddingLeft': '30px',  # Increased left padding to shift text right more
                    'borderLeft': '1px solid #eee',
                    'marginLeft': '15px',
                    'minHeight': '150px',  # Reduced minimum height for text container
                    'display': 'flex',
                    'flexDirection': 'column',
                    'justifyContent': 'center'
                }),
            ], className="machine-info-container", style={
                'display': 'flex',
                'flexDirection': 'row',
                'alignItems': 'center',
                'flexWrap': 'wrap',
                'width': '100%',
                'minHeight': '150px'  # Reduced minimum height for the whole container
            }),
        ], style={'height': 'auto'})  # Allow section 3-2 height to adjust

    @app.callback(
        Output("section-4", "children"),
        [Input("status-update-interval", "n_intervals"),
         Input("current-dashboard",       "data"),
         Input("language-preference-store", "data")],
        [State("app-state", "data"),
         State("app-mode", "data")],
        prevent_initial_call=True
    )
    def update_section_4(n_intervals, which, lang, app_state_data, app_mode):
        """Update section 4 with the color sort primary list.
    
        Each sensitivity's number and name are displayed above its image.
        """
        # only run when we’re in the “main” dashboard
        if which != "main":
            raise PreventUpdate
            # or return [no_update, no_update]
        # Tag definitions for easy updating
        PRIMARY_ACTIVE_TAG_PREFIX = "Settings.ColorSort.Primary"
        PRIMARY_ACTIVE_TAG_SUFFIX = ".IsAssigned"
        PRIMARY_NAME_TAG_PREFIX = "Settings.ColorSort.Primary"
        PRIMARY_NAME_TAG_SUFFIX = ".Name"
        PRIMARY_IMAGE_TAG_PREFIX = "Settings.ColorSort.Primary"
        PRIMARY_IMAGE_TAG_SUFFIX = ".SampleImage"
        
        # Define colors for each primary number
        primary_colors = {
            1: "green",       # Blue
            2: "lightgreen",      # Green
            3: "orange",     # Orange
            4: "blue",      # Black
            5: "#f9d70b",    # Yellow (using hex to ensure visibility)
            6: "magenta",    # Magenta
            7: "cyan",       # Cyan
            8: "red",        # Red
            9: "purple",
            10: "brown",
            11: "gray", 
            12: "lightblue"
        }
        
        # Define base64 image strings for demo mode fallback
        base64_image_strings = {
            1: base64_image_string1,
            2: base64_image_string2,
            3: base64_image_string3,
            4: base64_image_string4,
            5: base64_image_string5,
            6: base64_image_string6,
            7: base64_image_string7,
            8: base64_image_string8
        }
        
        # Determine if we're in Live or Demo mode
        mode = "demo"  # Default to demo mode
        if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
            mode = app_mode["mode"]
        
        # Define demo mode primary names and active status
        demo_primary_names = {
            1: "CORN",
            2: "SPOT",
            3: "GREEN",
            4: "SOY",
            5: "SPLIT",
            6: "DARKS",
            7: "BROKEN",
            8: "MOLD",
            9: "",
            10: "",
            11: "",
            12: ""
        }
        
        # For demo mode, all primaries are active except #5 (to show the inactive state)
        demo_primary_active = {i: (i != 5) for i in range(1, 13)}
        
        # Initialize lists for left and right columns
        left_column_items = []
        right_column_items = []
        
        # Define the image container style with WHITE background for both modes
        # Base style for the image containers.  Border color is set later based on
        # whether a sensitivity is assigned.
        image_container_style = {
            "height": "50px",
            "width": "50px",
            "marginRight": "0px",
            "border": "4px solid #ccc",  # Increased default border thickness
            "borderRadius": "3px",
            "display": "flex",
            "alignItems": "center",
            "justifyContent": "center",
            "overflow": "hidden",
            "padding": "0px",
            "backgroundColor": "#ffffff"  # Force white background for both light and dark mode
        }
        
        # Image style to fill the container
        image_style = {
            "height": "100%",
            "width": "100%",
            "objectFit": "contain",
        }
        
        if mode == "demo":
            # Demo mode - use predefined values and demo images
            for i in range(1, 13):
                name = demo_primary_names[i]
                is_active = demo_primary_active[i]
                    
                # Set styling based on active status
                if is_active:
                    text_color = primary_colors[i]
                    text_class = ""
                else:
                    text_color = "#aaaaaa"  # Gray for inactive
                    text_class = "text-muted"
                
                # Create text style with added bold font weight
                text_style = {
                    "color": text_color,
                    "display": "inline-block",
                    "verticalAlign": "middle",
                    "fontWeight": "bold",
                    "whiteSpace": "nowrap",
                }
                border_color = "green" if is_active else "red"
                if not is_active:
                    text_style["fontStyle"] = "italic"
                image_style_current = image_container_style.copy()
                image_style_current["border"] = f"4px solid {border_color}"
                
                # Create item with appropriate image or empty container
                if i <= 8 and i in base64_image_strings:  # First 8 items with images in demo mode
                    base64_str = base64_image_strings[i]
                    img_src = f"data:image/png;base64,{base64_str}" if not base64_str.startswith("data:") else base64_str
    
                    # Create item with image in bordered container
                    item = html.Div([
                        html.Span(
                            f"{i}. {name}",
                            style=text_style
                        ),
                        html.Div([
                            html.Img(
                                src=img_src,
                                style=image_style
                            )
                        ], style=image_style_current),
                    ],
                    className=f"mb-1 {text_class}",
                    style={"display": "flex", "flexDirection": "column", "alignItems": "center"})
                else:  # Items 9-12 or fallbacks - empty white container instead of image
                    item = html.Div([
                        html.Span(
                            f"{i}. {name}",
                            style=text_style
                        ),
                        html.Div([
                            # Nothing inside, just the white background
                        ], style=image_style_current),
                    ],
                    className=f"mb-1 {text_class}",
                    style={"display": "flex", "flexDirection": "column", "alignItems": "center"})
                
                # Add to appropriate column based on odd/even
                if i % 2 == 1:  # Odds on the left
                    left_column_items.append(item)
                else:          # Evens on the right
                    right_column_items.append(item)
        
        elif not app_state_data.get("connected", False):
            # When not connected, show placeholder list with empty white containers
            for i in range(1, 13):
                # Bold text style for not connected state
                not_connected_style = {
                    "display": "inline-block",
                    "verticalAlign": "middle",
                    "fontWeight": "bold",
                    "whiteSpace": "nowrap",
                }
                
                item = html.Div([
                    html.Span(
                        f"{i}) Not connected",
                        className="text-muted",
                        style=not_connected_style
                    ),
                    html.Div([], style=image_container_style),  # Empty white container
                ],
                className="mb-1",
                style={"display": "flex", "flexDirection": "column", "alignItems": "center"})
                
                # Add to appropriate column based on odd/even
                if i % 2 == 1:  # Odds on the left
                    left_column_items.append(item)
                else:          # Evens on the right
                    right_column_items.append(item)
        
        else:
            # Live mode - load images from OPC UA tags
            for i in range(1, 13):
                # Check if the primary is active
                is_active = True  # Default to active
                active_tag_name = f"{PRIMARY_ACTIVE_TAG_PREFIX}{i}{PRIMARY_ACTIVE_TAG_SUFFIX}"
                
                if active_tag_name in app_state.tags:
                    is_active = bool(app_state.tags[active_tag_name]["data"].latest_value)
                
                # Get primary name
                name = f"Primary {i}"  # Default name
                name_tag = f"{PRIMARY_NAME_TAG_PREFIX}{i}{PRIMARY_NAME_TAG_SUFFIX}"
                
                if name_tag in app_state.tags:
                    tag_value = app_state.tags[name_tag]["data"].latest_value
                    if tag_value is not None:
                        name = tag_value
                
                # Get sample image from OPC UA tag
                image_tag = f"{PRIMARY_IMAGE_TAG_PREFIX}{i}{PRIMARY_IMAGE_TAG_SUFFIX}"
                has_image = False
                image_src = None
                
                if image_tag in app_state.tags:
                    try:
                        image_data = app_state.tags[image_tag]["data"].latest_value
                        if image_data is not None:
                            # Check if the image data is already in the correct format
                            if isinstance(image_data, str):
                                if image_data.startswith("data:image"):
                                    # Already in data URL format
                                    image_src = image_data
                                    has_image = True
                                elif len(image_data) > 100:  # Assume it's base64 if it's a long string
                                    # Try to determine image type and create data URL
                                    # For now, assume PNG - you might need to detect the actual format
                                    image_src = f"data:image/png;base64,{image_data}"
                                    has_image = True
                            elif isinstance(image_data, bytes):
                                # Convert bytes to base64
                                base64_str = base64.b64encode(image_data).decode('utf-8')
                                image_src = f"data:image/png;base64,{base64_str}"
                                has_image = True
                    except Exception as e:
                        #logger.error(f"Error processing image data for Primary {i}: {e}")
                        has_image = False
                #else:
                    #logger.debug(f"Image tag {image_tag} not found in app_state.tags")
                
                # Set styling based on active status
                if is_active:
                    text_color = primary_colors[i]
                    text_class = ""
                else:
                    text_color = "#aaaaaa"  # Gray for inactive
                    text_class = "text-muted"
                
                # Create text style with added bold font weight
                text_style = {
                    "color": text_color,
                    "display": "inline-block",
                    "verticalAlign": "middle",
                    "fontWeight": "bold",
                    "whiteSpace": "nowrap",
                }
                border_color = "green" if is_active else "red"
                if not is_active:
                    text_style["fontStyle"] = "italic"
                image_style_current = image_container_style.copy()
                image_style_current["border"] = f"4px solid {border_color}"
    
                # Create item with image from OPC UA tag or empty white container
                if has_image and image_src:
                    item = html.Div([
                        html.Span(
                            f"{i}) {name}",
                            style=text_style
                        ),
                        html.Div([  # Wrapper div for the image with white background
                            html.Img(
                                src=image_src,
                                style=image_style,
                                title=f"Sample image for {name}"  # Add tooltip
                            )
                        ], style=image_style_current),
                    ],
                    className=f"mb-1 {text_class}",
                    style={"display": "flex", "flexDirection": "column", "alignItems": "center"})
                else:
                    # No image available - show empty white container
                    item = html.Div([
                        html.Span(
                            f"{i}) {name}",
                            style=text_style
                        ),
                        html.Div([  # Empty white container
                            # Nothing inside, just the white background
                        ], style=image_style_current),
                    ],
                    className=f"mb-1 {text_class}",
                    style={"display": "flex", "flexDirection": "column", "alignItems": "center"})
                
                # Add to appropriate column based on odd/even
                if i % 2 == 1:  # Odds on the left
                    left_column_items.append(item)
                else:          # Evens on the right
                    right_column_items.append(item)
        
        # Allow this panel to flex so it shares space with other sections
        container_style = {"flex": "1"}
        
        # Return two-column layout
        return html.Div([
            html.H5(tr("sensitivities_title", lang), className="mb-2 text-left"),
            
            # Create a row with two columns
            dbc.Row([
                # Left column - odd items
                dbc.Col(
                    html.Div(left_column_items),
                    width=6
                ),
    
                # Right column - even items
                dbc.Col(
                    html.Div(right_column_items),
                    width=6
                ),
            ]),
        ], style=container_style)

    @app.callback(
        Output("section-5-1", "children"),
        [Input("status-update-interval", "n_intervals"),
         Input("current-dashboard",       "data"),
         Input("historical-time-index",   "data"),
         Input("historical-data-cache",   "data"),
         Input("language-preference-store", "data")],
        [State("app-state", "data"),
         State("app-mode", "data"),
         State("active-machine-store", "data"),
         State("weight-preference-store", "data"),
         State("production-rate-unit", "data")],
        prevent_initial_call=True
    )
    def update_section_5_1(n_intervals, which, state_data, historical_data, lang, app_state_data, app_mode, active_machine_data, weight_pref, pr_unit):
    
        """Update section 5-1 with trend graph for objects per minute"""
         # only run when we’re in the “main” dashboard
        if which != "main":
            raise PreventUpdate
            # or return [no_update, no_update]
    
        # Tag definitions - Easy to update when actual tag names are available
        OBJECTS_PER_MIN_TAG = "Status.ColorSort.Sort1.Throughput.ObjectPerMin.Current"
        CAPACITY_TAG = "Status.ColorSort.Sort1.Throughput.KgPerHour.Current"
    
        # Determine which units to display
        units = pr_unit or "objects"
        if units == "capacity":
            section_title = tr("production_rate_capacity_title", lang)
            data_tag = CAPACITY_TAG
        else:
            section_title = tr("production_rate_objects_title", lang)
            data_tag = OBJECTS_PER_MIN_TAG
        
        # Fixed time range for X-axis (last 2 minutes with 1-second intervals)
        max_points = 120  # 2 minutes × 60 seconds
        
        # Determine if we're in Live or Demo mode
        mode = "demo"  # Default to demo mode
        if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
            mode = app_mode["mode"]
    
    
        if mode == "historical":
            hours = state_data.get("hours", 24) if isinstance(state_data, dict) else 24
            active_id = active_machine_data.get("machine_id") if active_machine_data else None
            hist_data = (
                historical_data if isinstance(historical_data, dict) and "capacity" in historical_data
                else get_historical_data(timeframe=f"{hours}h", machine_id=active_id)
            )
            times = hist_data["capacity"]["times"]
            values_lbs = hist_data["capacity"]["values"]
    
            x_data = [t.strftime("%H:%M:%S") if isinstance(t, datetime) else t for t in times]
            y_data = [convert_capacity_from_lbs(v, weight_pref) for v in values_lbs]
            if y_data:
                min_val = max(0, min(y_data) * 0.9)
                max_val = max(y_data) * 1.1
            else:
                min_val = 0
                max_val = 10000
        elif mode == "lab":
            mid = active_machine_data.get("machine_id") if active_machine_data else None
            _, times, totals = load_lab_totals(mid, active_counters=get_active_counter_flags(mid))
            x_data = [t.strftime("%H:%M:%S") if isinstance(t, datetime) else t for t in times]
            y_data = totals
            if y_data:
                min_val = max(0, min(y_data) * 0.9)
                max_val = max(y_data) * 1.1
            else:
                min_val = 0
                max_val = 10000

        elif mode in LIVE_LIKE_MODES and app_state_data.get("connected", False):
            # Live mode and connected - get real data
            tag_found = False
            current_value = 0
    
    
            
            # Check if the tag exists
            if data_tag in app_state.tags:
                tag_found = True
                tag_data = app_state.tags[data_tag]['data']
                
                # Get current value
                current_value = tag_data.latest_value if tag_data.latest_value is not None else 0
                if units == "capacity":
                    current_value = convert_capacity_from_kg(current_value, weight_pref)
                
                # Get historical data
                timestamps = tag_data.timestamps
                values = tag_data.values
                if units == "capacity":
                    values = [convert_capacity_from_kg(v, weight_pref) for v in values]
                
                # If we have data, create the time series
                if timestamps and values:
                    # Ensure we only use the most recent data points (up to max_points)
                    if len(timestamps) > max_points:
                        timestamps = timestamps[-max_points:]
                        values = values[-max_points:]
                    
                    # Format times for display
                    x_data = [ts.strftime("%H:%M:%S") for ts in timestamps]
                    y_data = values
                    
                    # Determine min and max values for y-axis with some padding
                    if len(y_data) > 0:
                        min_val = max(0, min(y_data) * 0.9) if min(y_data) > 0 else 0
                        max_val = max(y_data) * 1.1 if max(y_data) > 0 else 10000
                    else:
                        min_val = 0
                        max_val = 100000
                else:
                    # No historical data yet, create empty chart
                    current_time = datetime.now()
                    x_data = [(current_time - timedelta(seconds=i)).strftime("%H:%M:%S") for i in range(max_points)]
                    x_data.reverse()  # Put in chronological order
                    y_data = [None] * max_points
                    min_val = 0
                    max_val = 10000
            else:
                # Tag not found - create dummy data
                current_time = datetime.now()
                x_data = [(current_time - timedelta(seconds=i)).strftime("%H:%M:%S") for i in range(max_points)]
                x_data.reverse()  # Put in chronological order
                y_data = [None] * max_points
                min_val = 0
                max_val = 10000
        else:
            # Demo mode or not connected - use the original code
            # Generate dummy data for demonstration
            current_time = datetime.now()
            x_data = [(current_time - timedelta(seconds=i)).strftime("%H:%M:%S") for i in range(max_points)]
            x_data.reverse()  # Put in chronological order
            
            # Demo mode - create realistic looking data
            if mode == "demo":
                if units == "capacity":
                    # Base around 50,000 lbs/hr converted from kg
                    base_value = convert_capacity_from_kg(50000 / 2.205, weight_pref)
                else:
                    # Start with base value of 5000 objects per minute
                    base_value = 5000
                
                # Create random variations around the base value
                np.random.seed(int(current_time.timestamp()) % 1000)  # Seed with current time for variety
                var_scale = 2000 if units == "capacity" else 1000
                variations = np.random.normal(0, var_scale, max_points)
                
                # Create a slightly rising trend
                trend = np.linspace(0, 15, max_points)  # Rising trend from 0 to 15
                
                # Add some cyclical pattern
                cycles = 10 * np.sin(np.linspace(0, 4*np.pi, max_points))  # Sine wave with amplitude 10
                
                # Combine base value, variations, trend, and cycles
                y_data = [max(0, base_value + variations[i] + trend[i] + cycles[i]) for i in range(max_points)]
                
                min_val = base_value * 0.8 if units == "capacity" else 3000
                max_val = max(y_data) * 1.1  # 10% headroom
            else:
                # Not connected - empty chart
                y_data = [None] * max_points
                min_val = 3000 if units != "capacity" else 0
                max_val = 10000
        
        # Create figure
        fig = go.Figure()
        
        # Add trace
        fig.add_trace(go.Scatter(
            x=x_data,
            y=y_data,
            mode='lines',
            name='Capacity' if units == "capacity" else 'Objects/Min',
            line=dict(color='#1f77b4', width=2)
        ))
    
        step = max(1, len(x_data) // 5)
        
        # Update layout
        fig.update_layout(
            title=None,
            xaxis=dict(
                showgrid=True,
                gridcolor='rgba(211,211,211,0.3)',
                tickmode='array',
                tickvals=list(range(0, len(x_data), step)),
                ticktext=[x_data[i] for i in range(0, len(x_data), step) if i < len(x_data)],
            ),
            yaxis=dict(
                title=None,
                showgrid=True,
                gridcolor='rgba(211,211,211,0.3)',
                range=[min_val, max_val]
            ),
            margin=dict(l=5, r=5, t=5, b=5),
            height=200,
            plot_bgcolor='var(--chart-bg)',
            paper_bgcolor='var(--chart-bg)',
            hovermode='closest',
            showlegend=False
        )
        
        # Include the historical indicator directly in the header so the
        # graph height remains unchanged when toggling modes.
        header = f"{section_title} (Historical View)" if mode == "historical" else section_title
    
        children = [
            dbc.Row([
                dbc.Col(html.H5(header, className="mb-0"), width=9),
                dbc.Col(
                    dbc.Button(
                        tr("units_button", lang),
                        id={"type": "open-production-rate-units", "index": 0},
                        color="primary",
                        size="sm",
                        className="float-end",
                    ),
                    width=3,
                ),
            ], className="mb-2 align-items-center")
        ]
    
    
    
        children.append(
            dcc.Graph(
                id='trend-graph',
                figure=fig,
                config={'displayModeBar': False, 'responsive': True},
                style={'width': '100%', 'height': '100%'}
            )
        )
    
        return html.Div(children)

    @app.callback(
        Output("alarm-data", "data"),
        [Input("status-update-interval", "n_intervals")],
        [State("app-state", "data")]
    )
    def update_alarms_store(n_intervals, app_state_data):
        """Update the alarms data store from the counter values and check for threshold violations"""
        global previous_counter_values, threshold_settings, threshold_violation_state

        # Determine how counter values should be interpreted
        mode = threshold_settings.get("counter_mode", "counts") if isinstance(threshold_settings, dict) else "counts"
        if mode == "percent":
            total_val = sum(previous_counter_values)
            values = [
                (v / total_val * 100) if total_val else 0
                for v in previous_counter_values
            ]
        else:
            values = previous_counter_values

        # Get current time
        current_time = datetime.now()

        # Check for alarms
        alarms = []
        for i, value in enumerate(values):
            counter_num = i + 1
            
            # Safely check if counter_num exists in threshold_settings and is a dictionary
            if counter_num in threshold_settings and isinstance(threshold_settings[counter_num], dict):
                settings = threshold_settings[counter_num]
                violation = False
                is_high = False  # Track which threshold is violated (high or low)
                
                # Check for threshold violations
                if 'min_enabled' in settings and settings['min_enabled'] and value < settings['min_value']:
                    violation = True
                    alarms.append(f"Sens. {counter_num} below min threshold")
                elif 'max_enabled' in settings and settings['max_enabled'] and value > settings['max_value']:
                    violation = True
                    is_high = True
                    alarms.append(f"Sens. {counter_num} above max threshold")
                
                # Get violation state for this counter
                violation_state = threshold_violation_state[counter_num]
                
                # If email notifications are enabled
                if threshold_settings.get('email_enabled', False):
                    email_minutes = threshold_settings.get('email_minutes', 2)
                    
                    # If now violating but wasn't before
                    if violation and not violation_state['is_violating']:
                        # Start tracking this violation
                        violation_state['is_violating'] = True
                        violation_state['violation_start_time'] = current_time
                        violation_state['email_sent'] = False
                        logger.info(f"Started tracking threshold violation for Sensitivity {counter_num}")
                    
                    # If still violating
                    elif violation and violation_state['is_violating']:
                        # Check if it's been violating long enough to send an email
                        if not violation_state['email_sent']:
                            time_diff = (current_time - violation_state['violation_start_time']).total_seconds()
                            if time_diff >= (email_minutes * 60):
                                # Send the email
                                email_sent = send_threshold_email(counter_num, is_high)
                                if email_sent:
                                    violation_state['email_sent'] = True
                                    logger.info(f"Sent threshold violation email for Sensitivity {counter_num}")
                    
                    # If no longer violating
                    elif not violation and violation_state['is_violating']:
                        # Reset the violation state
                        violation_state['is_violating'] = False
                        violation_state['violation_start_time'] = None
                        violation_state['email_sent'] = False
                        logger.info(f"Reset threshold violation for Sensitivity {counter_num}")
        
        return {"alarms": alarms}

    @app.callback(
        Output("section-5-2", "children"),
        [Input("status-update-interval", "n_intervals"),
         Input("current-dashboard",       "data"),
         Input("historical-time-index",   "data"),
         Input("historical-data-cache",   "data"),
         Input("language-preference-store", "data")],
        [State("app-state", "data"),
         State("app-mode", "data"),
         State("active-machine-store", "data"),
         State("counter-view-mode", "data")],
        prevent_initial_call=True
    )
    def update_section_5_2(n_intervals, which, state_data, historical_data, lang, app_state_data, app_mode, active_machine_data, counter_mode):
        """Update section 5-2 with bar chart for counter values and update alarm data"""
        
        # only run when we’re in the “main” dashboard
        if which != "main":
            raise PreventUpdate
            # or return [no_update, no_update]
        global previous_counter_values, threshold_settings
    
        # Ensure we have a full set of values to work with
        if not previous_counter_values or len(previous_counter_values) < 12:
            previous_counter_values = [0] * 12
        
        # Define title for the section
        section_title = tr("sensitivity_rates_title", lang)
        
        # Always read counter rate values from OPC. Percent view only changes
        # how the bar chart is displayed.
        TAG_PATTERN = "Status.ColorSort.Sort1.DefectCount{}.Rate.Current"
        
        # Define colors for each primary/counter number
        counter_colors = {
            1: "green",       # Blue
            2: "lightgreen",      # Green
            3: "orange",     # Orange
            4: "blue",      # Black
            5: "#f9d70b",    # Yellow (using hex to ensure visibility)
            6: "magenta",    # Magenta
            7: "cyan",       # Cyan
            8: "red",        # Red
            9: "purple",
            10: "brown",
            11: "gray",
            12: "lightblue"
        }
        
        # Get mode (live, demo, or historical)
        mode = "demo"  # Default to demo mode
        if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
            mode = app_mode["mode"]
        
        # Generate values based on mode
        if mode == "historical":
            hours = state_data.get("hours", 24) if isinstance(state_data, dict) else 24
            active_id = active_machine_data.get("machine_id") if active_machine_data else None
            historical_data = (
                historical_data
                if isinstance(historical_data, dict) and 1 in historical_data
                else get_historical_data(timeframe=f"{hours}h", machine_id=active_id)
            )
            
            # Use the average value for each counter over the timeframe
            new_counter_values = []
            for i in range(1, 13):
                vals = historical_data[i]["values"]
                if vals:
                    avg_val = sum(vals) / len(vals)
                    new_counter_values.append(avg_val)
                else:
                    new_counter_values.append(50)
    
            # Store the new values for the next update
            previous_counter_values = new_counter_values.copy()
            #logger.debug("Section 5-2 values (historical mode): %s", new_counter_values)
        elif mode == "lab":
            mid = active_machine_data.get("machine_id") if active_machine_data else None
            rates = load_last_lab_counters(mid)
            new_counter_values = [r * 60 for r in rates]
            previous_counter_values = new_counter_values.copy()
            #logger.debug("Section 5-2 values (lab mode): %s", new_counter_values)
        elif mode in LIVE_LIKE_MODES and app_state_data.get("connected", False):
            # Live mode: get values from OPC UA
            # Use the tag pattern provided for each counter
            new_counter_values = []
            for i in range(1, 13):
                # Construct the tag name using the provided pattern
                tag_name = TAG_PATTERN.format(i)
    
                # Check if the tag exists
                if tag_name in app_state.tags:
                    value = app_state.tags[tag_name]["data"].latest_value
                    if value is None:
                        # If tag exists but value is None, keep previous value
                        value = previous_counter_values[i-1]
                    new_counter_values.append(value)
                else:
                    # Tag not found - keep previous value
                    new_counter_values.append(previous_counter_values[i-1])
    
            # Store the new values for the next update
            previous_counter_values = new_counter_values.copy()
            #logger.debug("Section 5-2 values (live mode): %s", new_counter_values)
        elif mode == "demo":
            # Demo mode: generate synthetic values
            new_counter_values = []
            for i, prev_value in enumerate(previous_counter_values):
                # Determine maximum change (up to ±20)
                max_change = min(20, prev_value - 10)  # Ensure we don't go below 10
    
                # Fix: Convert max_change to an integer
                max_change_int = int(max_change)
    
                # Use the integer version in randint
                change = random.randint(-max_change_int, 20)
    
                # Calculate new value with bounds
                new_value = max(10, min(180, prev_value + change))
    
                # Add to the list
                new_counter_values.append(new_value)
    
            # Store the new values for the next update
            previous_counter_values = new_counter_values.copy()
            #logger.debug("Section 5-2 values (demo mode): %s", new_counter_values)
        else:
            # Live mode but not connected - keep the last values
            new_counter_values = previous_counter_values.copy()
            #logger.debug("Section 5-2 values (disconnected): using previous values")
        
        # Create counter names
        counter_names = [f"{i}" for i in range(1, 13)]
        
        # Convert values for display if percent mode is selected
        if counter_mode == "percent":
            total_val = sum(new_counter_values)
            display_values = [
                (v / total_val * 100) if total_val else 0 for v in new_counter_values
            ]
        else:
            display_values = new_counter_values

        # Create figure with our data
        fig = go.Figure()

        # Use a single bar trace with all data
        fig.add_trace(go.Bar(
            x=counter_names,  # Use all counter names as x values
            y=display_values,  # Display values depend on view mode
            marker_color=[counter_colors.get(i, 'gray') for i in range(1, 13)],  # Set colors per bar
            hoverinfo='text',  # Keep hover info
            hovertext=[f"Sensitivity {i}: {display_values[i-1]:.2f}" for i in range(1, 13)]  # Custom hover text with 2 decimal places

        ))
        
        # Add horizontal min threshold lines for each counter if enabled
        for i, counter in enumerate(counter_names):
            counter_num = i + 1
            # Check if counter_num exists in threshold_settings and is a dictionary
            if counter_num in threshold_settings and isinstance(threshold_settings[counter_num], dict):
                settings = threshold_settings[counter_num]
                
                if 'min_enabled' in settings and settings['min_enabled']:
                    fig.add_shape(
                        type="line",
                        x0=i - 0.4,  # Start slightly before the bar
                        x1=i + 0.4,  # End slightly after the bar
                        y0=settings['min_value'],
                        y1=settings['min_value'],
                        line=dict(
                            color="black",
                            width=2,
                            dash="solid",
                        ),
                    )
        
        # Add horizontal max threshold lines for each counter if enabled
        for i, counter in enumerate(counter_names):
            counter_num = i + 1
            # Check if counter_num exists in threshold_settings and is a dictionary
            if counter_num in threshold_settings and isinstance(threshold_settings[counter_num], dict):
                settings = threshold_settings[counter_num]
                
                if 'max_enabled' in settings and settings['max_enabled']:
                    fig.add_shape(
                        type="line",
                        x0=i - 0.4,  # Start slightly before the bar
                        x1=i + 0.4,  # End slightly after the bar
                        y0=settings['max_value'],
                        y1=settings['max_value'],
                        line=dict(
                            color="red",
                            width=2,
                            dash="solid",
                        ),
                    )
        
        # Calculate max value for y-axis scaling
        if counter_mode == "percent":
            # Percent view caps the axis at 100 and uses display values
            max_value = max(display_values) if display_values else 0
            y_max = min(max_value * 1.1, 100)
            if y_max < 5:
                y_max = 5
        else:
            # Counts view - include enabled thresholds in the calculation
            all_values = new_counter_values.copy()
            for counter_num, settings in threshold_settings.items():
                if isinstance(counter_num, int) and isinstance(settings, dict):
                    if 'max_enabled' in settings and settings['max_enabled']:
                        all_values.append(settings['max_value'])

            max_value = max(all_values) if all_values else 100
            y_max = max(100, max_value * 1.1)
        
        # Update layout
        fig.update_layout(
            title=None,
            xaxis=dict(
                title=None,
                showgrid=False,
                tickangle=0,
            ),
            yaxis=dict(
                title=None,
                showgrid=True,
                gridcolor='rgba(211,211,211,0.3)',
                range=[0, y_max]  # Dynamic range based on data and thresholds
            ),
            margin=dict(l=5, r=5, t=0, b=20),  # Increased bottom margin for rotated labels
            height=198,  # Increased height since we have more space now
            plot_bgcolor='var(--chart-bg)',
            paper_bgcolor='var(--chart-bg)',
            showlegend=False,
        )
        
        # Create the section content
        section_content = html.Div([
            # Header row with title and settings button
            dbc.Row([
                dbc.Col(html.H5(section_title + (" (Historical)" if mode == "historical" else ""), className="mb-0"), width=9),
                dbc.Col(
                    dbc.Button(tr("thresholds_button", lang),
                            id={"type": "open-threshold", "index": 0},
                            color="primary",
                            size="sm",
                            className="float-end"),
                    width=3
                )
            ], className="mb-2 align-items-center"),
            
            # Bar chart
            dcc.Graph(
                id='counter-bar-chart',
                figure=fig,
                config={'displayModeBar': False, 'responsive': True},
                style={'width': '100%', 'height': '100%'}
            )
        ])
        
        # Return the section content
        return section_content

    @app.callback(
        Output("section-6-1", "children"),
        [Input("status-update-interval", "n_intervals"),
         Input("current-dashboard", "data"),
         Input("historical-time-index", "data"),
         Input("language-preference-store", "data")],
        [State("app-state", "data"),
         State("app-mode", "data"),
         State("active-machine-store", "data")],
        prevent_initial_call=True,
    )
    def update_section_6_1(n_intervals, which, state_data, lang, app_state_data, app_mode, active_machine_data):
        """Update section 6-1 with trend graph for the 12 counters, supporting historical data."""
        mem_utils.log_memory_if_high()
        if which != "main":
            raise PreventUpdate
        global previous_counter_values, display_settings

        if not previous_counter_values or len(previous_counter_values) < 12:
            previous_counter_values = [0] * 12

        section_title = tr("counter_values_trend_title", lang)

        counter_colors = {
            1: "green",
            2: "lightgreen",
            3: "orange",
            4: "blue",
            5: "#f9d70b",
            6: "magenta",
            7: "cyan",
            8: "red",
            9: "purple",
            10: "brown",
            11: "gray",
            12: "lightblue",
        }

        mode = "demo"
        if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
            mode = app_mode["mode"]

        if mode == "historical":
            hours = state_data.get("hours", 24) if isinstance(state_data, dict) else 24
            active_id = active_machine_data.get("machine_id") if active_machine_data else None
            historical_data = get_historical_data(timeframe=f"{hours}h", machine_id=active_id)

            fig = go.Figure()
            for i in range(1, 13):
                if display_settings.get(i, True):
                    counter_name = f"Counter {i}"
                    color = counter_colors.get(i, "gray")
                    times = historical_data[i]['times']
                    values = historical_data[i]['values']
                    time_labels = [t.strftime("%H:%M:%S") if isinstance(t, datetime) else t for t in times]
                    if times and values:
                        fig.add_trace(go.Scatter(
                            x=time_labels,
                            y=values,
                            mode='lines',
                            name=counter_name,
                            line=dict(color=color, width=2),
                            hoverinfo='text',
                            hovertext=[f"{counter_name}: {value}" for value in values],
                        ))

            ref_times = historical_data[1]['times'] if historical_data[1]['times'] else []
            label_list = [t.strftime('%H:%M:%S') if isinstance(t, datetime) else t for t in ref_times]
            step = max(1, len(label_list) // 5) if label_list else 1

            hist_values = [historical_data[i]['values'][-1] if historical_data[i]['values'] else None for i in range(1, 13)]
            #logger.debug("Section 6-1 latest values (historical mode): %s", hist_values)

            max_hist_value = 0
            for i in range(1, 13):
                if display_settings.get(i, True):
                    vals = historical_data[i]["values"]
                    if vals:
                        max_hist_value = max(max_hist_value, max(vals))

            yaxis_range = [0, 10] if max_hist_value < 10 else [0, None]

            fig.update_layout(
                title=None,
                xaxis=dict(
                    showgrid=False,
                    gridcolor='rgba(211,211,211,0.3)',
                    rangeslider=dict(visible=False),
                    tickmode='array',
                    tickvals=list(range(0, len(label_list), step)) if label_list else [],
                    ticktext=[label_list[i] for i in range(0, len(label_list), step) if i < len(label_list)] if label_list else [],
                ),
                yaxis=dict(
                    title=None,
                    showgrid=False,
                    gridcolor='rgba(211,211,211,0.3)',
                    range=yaxis_range,
                ),
                margin=dict(l=5, r=5, t=5, b=5),
                height=200,
                plot_bgcolor='var(--chart-bg)',
                paper_bgcolor='var(--chart-bg)',
                hovermode='closest',
                showlegend=False,
            )

            return html.Div([
                dbc.Row([
                    dbc.Col(html.H5(f"{section_title} (Historical View)", className="mb-0"), width=9),
                    dbc.Col(
                        dbc.Button(tr("display_button", lang),
                                   id={"type": "open-display", "index": 0},
                                   color="primary",
                                   size="sm",
                                   className="float-end"),
                        width=3,
                    ),
                ], className="mb-2 align-items-center"),
                dcc.Graph(
                    id='counter-trend-graph',
                    figure=fig,
                    config={'displayModeBar': False, 'responsive': True},
                    style={'width': '100%', 'height': '100%'}
                ),
            ])

        if not hasattr(app_state, 'counter_history'):
            app_state.counter_history = {i: {'times': [], 'values': []} for i in range(1, 13)}

        current_time = datetime.now()

        if mode in LIVE_LIKE_MODES and app_state_data.get("connected", False):
            for i, value in enumerate(previous_counter_values):
                counter_utils.add_data_point(app_state.counter_history, i + 1, current_time, value)
        elif mode == "demo":
            for i, value in enumerate(previous_counter_values):
                counter_utils.add_data_point(app_state.counter_history, i + 1, current_time, value)
        else:
            for i in range(1, 13):
                prev_vals = app_state.counter_history[i]['values']
                prev_value = prev_vals[-1] if prev_vals else 0
                counter_utils.add_data_point(app_state.counter_history, i, current_time, prev_value)

        latest_values = [app_state.counter_history[i]['values'][-1] if app_state.counter_history[i]['values'] else None for i in range(1, 13)]
        #logger.debug("Section 6-1 latest values (%s mode): %s", mode, latest_values)

        fig = go.Figure()

        for i in range(1, 13):
            if display_settings.get(i, True):
                counter_name = f"Counter {i}"
                color = counter_colors.get(i, "gray")
                times = app_state.counter_history[i]['times']
                values = app_state.counter_history[i]['values']
                time_labels = [t.strftime("%H:%M:%S") for t in times]
                if times and values:
                    fig.add_trace(go.Scatter(
                        x=time_labels,
                        y=values,
                        mode='lines',
                        name=counter_name,
                        line=dict(color=color, width=2),
                        hoverinfo='text',
                        hovertext=[f"{counter_name}: {value}" for value in values],
                    ))

        max_live_value = 0
        for i in range(1, 13):
            if display_settings.get(i, True):
                vals = app_state.counter_history[i]["values"]
                if vals:
                    max_live_value = max(max_live_value, max(vals))

        yaxis_range = [0, 10] if max_live_value < 10 else [0, None]

        fig.update_layout(
            title=None,
            xaxis=dict(
                showgrid=False,
                gridcolor='rgba(211,211,211,0.3)',
                tickmode='array',
                tickvals=list(range(0, len(time_labels), max(1, len(time_labels) // 5))) if time_labels else [],
                ticktext=[time_labels[i] for i in range(0, len(time_labels),
                                                    max(1, len(time_labels) // 5))
                        if i < len(time_labels)] if time_labels else [],
            ),
            yaxis=dict(
                title=None,
                showgrid=False,
                gridcolor='rgba(211,211,211,0.3)',
                range=yaxis_range,
            ),
            margin=dict(l=5, r=5, t=5, b=5),
            height=200,
            plot_bgcolor='var(--chart-bg)',
            paper_bgcolor='var(--chart-bg)',
            hovermode='closest',
            showlegend=False,
        )

        return html.Div([
            dbc.Row([
                dbc.Col(html.H5(section_title, className="mb-0"), width=9),
                dbc.Col(
                    dbc.Button(tr("display_button", lang),
                               id={"type": "open-display", "index": 0},
                               color="primary",
                               size="sm",
                               className="float-end"),
                    width=3,
                ),
            ], className="mb-2 align-items-center"),
            dcc.Graph(
                id='counter-trend-graph',
                figure=fig,
                config={'displayModeBar': False, 'responsive': True},
                style={'width': '100%', 'height': '100%'}
            ),
        ])

    @app.callback(
        Output("section-6-2", "children"),
        [Input("alarm-data", "data"),
         Input("current-dashboard",       "data"),
         Input("status-update-interval", "n_intervals"),
         Input("language-preference-store", "data")],
        prevent_initial_call=True
    )
    def update_section_6_2(alarm_data,which, n_intervals, lang):
        """Update section 6-2 with alarms display in two columns"""
         # only run when we’re in the “main” dashboard
        if which != "main":
            raise PreventUpdate
            # or return [no_update, no_update]
        # Set title for the section
        section_title = tr("sensitivity_threshold_alarms_title", lang)
        
        # Get alarms from the data store
        alarms = alarm_data.get("alarms", []) if alarm_data else []
    
        def _translate_alarm(alarm):
            if alarm.startswith("Sens."):
                parts = alarm.split()
                if len(parts) >= 3:
                    num = parts[1]
                    if "below" in alarm:
                        return tr("sensitivity_below_min", lang).format(num=num)
                    elif "above" in alarm:
                        return tr("sensitivity_above_max", lang).format(num=num)
            return alarm
    
        translated_alarms = [_translate_alarm(a) for a in alarms]
        
        # Create alarm display with two columns
        if alarms:
            # Split alarms into two columns
            mid_point = len(alarms) // 2 + len(alarms) % 2  # Ceiling division to balance columns
            left_alarms = translated_alarms[:mid_point]
            right_alarms = translated_alarms[mid_point:]
            
            # Create left column items
            left_items = [html.Li(alarm, className="text-danger mb-1") for alarm in left_alarms]
            
            # Create right column items
            right_items = [html.Li(alarm, className="text-danger mb-1") for alarm in right_alarms]
            
            # Create two-column layout
            alarm_display = html.Div([
                html.Div(tr("active_alarms_title", lang), className="fw-bold text-danger mb-2"),
                dbc.Row([
                    # Left column
                    dbc.Col(
                        html.Ul(left_items, className="ps-3 mb-0"),
                        width=6
                    ),
                    # Right column
                    dbc.Col(
                        html.Ul(right_items, className="ps-3 mb-0"),
                        width=6
                    ),
                ]),
            ])
        else:
            # No alarms display
            alarm_display = html.Div([
                html.Div("No active alarms", className="text-success")
            ])
        
        # Return the section content with fixed height
        return html.Div([
            html.H5(section_title, className="text-center mb-2"),
            
            # Alarms display with fixed height
            dbc.Card(
                dbc.CardBody(
                    alarm_display, 
                    className="p-2 overflow-auto",  # Add overflow-auto for scrolling if needed
                    # Scale alarm display height with viewport
                    style={"height": "205px"}
                ),
                className="h-100"
            ),
            
            # Timestamp
            
        ])

    @app.callback(
        Output("section-7-1", "children"),
        [Input("status-update-interval", "n_intervals"),
         Input("current-dashboard",       "data"),
         Input("language-preference-store", "data")],
        [State("app-state", "data"),
         State("app-mode", "data")],
        prevent_initial_call=True
    )
    def update_section_7_1(n_intervals, which, lang, app_state_data, app_mode):
        """Update section 7-1 with air pressure gauge"""
        # only run when we’re in the “main” dashboard
        if which != "main":
            raise PreventUpdate
            # or return [no_update, no_update]
    
        # Tag definition for air pressure - Easy to update when actual tag name is available
        AIR_PRESSURE_TAG = "Status.Environmental.AirPressurePsi"
        
        # Define gauge configuration
        min_pressure = 0
        max_pressure = 100
        
        # Define color ranges for gauge based on requirements
        red_range_low = [0, 30]       # Critical low range
        yellow_range = [31, 50]       # Warning range
        green_range = [51, 75]        # Normal range
        red_range_high = [76, 100]    # Critical high range
        
        # Determine if we're in Live or Demo mode
        mode = "demo"  # Default to demo mode
        if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
            mode = app_mode["mode"]
        
        # Get air pressure value based on mode
        if mode in LIVE_LIKE_MODES and app_state_data.get("connected", False):
            # Live mode: get value from OPC UA tag
            if AIR_PRESSURE_TAG in app_state.tags:
                # Read the actual value from the tag
                air_pressure = (app_state.tags[AIR_PRESSURE_TAG]["data"].latest_value)/100
                if air_pressure is None:
                    air_pressure = 0  # Default to 0 if tag exists but value is None
            else:
                # Tag not found, use 0 as per requirement
                air_pressure = 0
        else:
            # Demo mode: generate a realistic air pressure value with limited variation
            # Use timestamp for some variation in the demo
            timestamp = int(datetime.now().timestamp())
            
            # Generate value that stays very close to 65 PSI (±3 PSI maximum variation)
            base_value = 65  # Base in middle of green range
            # Use a small sine wave variation (±3 PSI max)
            variation = 3 * math.sin(timestamp / 10)  # Limited to ±3 PSI
            air_pressure = base_value + variation
        
        # Determine indicator color based on pressure value
        if 0 <= air_pressure <= 30:
            indicator_color = "red"
            status_text = "Critical Low"
            status_color = "danger"
        elif 31 <= air_pressure <= 50:
            indicator_color = "yellow"
            status_text = "Warning Low"
            status_color = "warning"
        elif 51 <= air_pressure <= 75:
            indicator_color = "green"
            status_text = "Normal"
            status_color = "success"
        else:  # 76-100
            indicator_color = "red"
            status_text = "Critical High"
            status_color = "danger"
        
        # Create the gauge figure
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=air_pressure,
            domain={'x': [0, 1], 'y': [0, 1]},
            #title={'text': "Air Pressure", 'font': {'size': 14}},
            gauge={
                'axis': {'range': [min_pressure, max_pressure], 'tickwidth': 1, 'tickcolor': "darkblue"},
                'bar': {'color': indicator_color},  # Use dynamic color based on value
                'bgcolor': "#d3d3d3",  # Light grey background
                'borderwidth': 2,
                'bordercolor': "gray",
                'threshold': {
                    'line': {'color': "darkgray", 'width': 4},
                    'thickness': 0.75,
                    'value': air_pressure
                }
            }
        ))
        
        # Update layout for the gauge
        fig.update_layout(
            height=200,
            margin=dict(l=10, r=10, t=30, b=10),
            paper_bgcolor='var(--chart-bg)',  # Use grey paper background
            plot_bgcolor='var(--chart-bg)',   # Use grey plot background
            font={'color': "darkblue", 'family': "Arial"}
        )
        
        return html.Div([
            html.H5(tr("air_pressure_title", lang), className="text-left mb-1"),
            # Gauge chart
            dcc.Graph(
                figure=fig,
                config={'displayModeBar': False, 'responsive': True},
                style={'width': '100%', 'height': '100%'}
            ),
            
            # Status text below the gauge
            #html.Div([
            #    html.Span("Status: ", className="fw-bold me-1"),
            #    html.Span(status_text, className=f"text-{status_color}")
            #], className="text-center mt-2")
        ])

    @app.callback(
        Output("section-7-2", "children"),
        [Input("status-update-interval", "n_intervals"),
         Input("current-dashboard",       "data"),
         Input("historical-time-index",   "data"),
         Input("language-preference-store", "data")],
        [State("app-state", "data"),
         State("app-mode", "data"),
         State("active-machine-store", "data")],
        prevent_initial_call=True
    )
    def update_section_7_2(n_intervals, which, time_state, lang, app_state_data, app_mode, active_machine_data):
        """Update section 7-2 with Machine Control Log"""
        # only run when we're in the "main" dashboard
        if which != "main":
            raise PreventUpdate
            
        global prev_values, prev_active_states, prev_preset_names, machine_control_log
    
        machine_id = active_machine_data.get("machine_id") if active_machine_data else None
    
        # Determine current mode (live or demo)
        mode = "demo"
        if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
            mode = app_mode["mode"]
        
        #logger.debug("Section 7-2 callback triggered at %s", datetime.now())
        #logger.debug("Section 7-2: mode=%s, connected=%s", mode, app_state_data.get("connected", False))
        #logger.debug("Section 7-2 Debug: machine_id=%s", machine_id)
        #logger.debug("Section 7-2 Debug: MONITORED_RATE_TAGS=%s", MONITORED_RATE_TAGS)
        #logger.debug("Section 7-2 Debug: prev_values keys=%s", list(prev_values.get(machine_id, {}).keys()))

        # Avoid dumping the entire tag list every cycle
        #logger.debug("app_state tag count: %s", len(app_state.tags))

    
        # Live monitoring of feeder rate tags and sensitivity assignments
        if mode in LIVE_LIKE_MODES and app_state_data.get("connected", False) and machine_id is not None:
            try:
                # Initialize machine_prev dictionaries if they don't exist
                if machine_id not in prev_values:
                    prev_values[machine_id] = {}
                    logger.debug("Initialized prev_values for machine %s", machine_id)
                if machine_id not in prev_active_states:
                    prev_active_states[machine_id] = {}
                    #logger.debug("Initialized prev_active_states for machine %s", machine_id)
                
                machine_prev = prev_values[machine_id]
                machine_prev_active = prev_active_states[machine_id]
    
                # Monitor feeder rate changes
                for opc_tag, friendly_name in MONITORED_RATE_TAGS.items():
                    try:
                        if opc_tag in app_state.tags:
                            new_val = app_state.tags[opc_tag]["data"].latest_value
                            prev_val = machine_prev.get(opc_tag)
                            #logger.debug("Tag %s: new_val=%s, prev_val=%s", opc_tag, new_val, prev_val)
    
                            if prev_val is not None and new_val is not None and new_val != prev_val:
                                logger.debug("CHANGE DETECTED! %s: %s -> %s", opc_tag, prev_val, new_val)
                                try:
                                    #logger.debug("Rate %s changed from %s to %s", opc_tag, prev_val, new_val)
                                    add_control_log_entry(friendly_name, prev_val, new_val, machine_id=machine_id)
                                    #logger.debug("LOG ENTRY ADDED for %s", friendly_name)
                                except Exception as e:
                                    logger.error(f"ERROR adding log entry: {e}")
    
                            machine_prev[opc_tag] = new_val
                        else:
                            logger.warning(f"Feeder tag {opc_tag} not found in app_state.tags")
                    except Exception as e:
                        logger.error(f"Error monitoring feeder tag {opc_tag}: {e}")
    
                # Monitor sensitivity assignment changes  
                #logger.debug("Starting sensitivity tag checks")
                for opc_tag, sens_num in SENSITIVITY_ACTIVE_TAGS.items():
                    try:
                        if opc_tag in app_state.tags:
                            new_val = app_state.tags[opc_tag]["data"].latest_value
                            prev_val = machine_prev_active.get(opc_tag)
                            #logger.info(f"Sensitivity {sens_num} Tag {opc_tag}: new_val={new_val}, prev_val={prev_val}")
                            
                            if prev_val is not None and new_val is not None and bool(new_val) != bool(prev_val):
                                #logger.info(f"SENSITIVITY CHANGE DETECTED! Sens {sens_num}: {bool(prev_val)} -> {bool(new_val)}")
                                try:
                                    add_activation_log_entry(sens_num, bool(new_val), machine_id=machine_id)
                                    #logger.info(f"SENSITIVITY LOG ENTRY ADDED for Sensitivity {sens_num}")
                                except Exception as e:
                                    logger.error(f"ERROR adding sensitivity log entry: {e}")
                                    
                            machine_prev_active[opc_tag] = new_val
                        else:
                            if opc_tag not in warned_sensitivity_tags:
                                logger.warning(
                                    "Sensitivity tag %s missing from app_state.tags",
                                    opc_tag,
                                )
                                warned_sensitivity_tags.add(opc_tag)
                    except Exception as e:
                        logger.error(f"Error monitoring sensitivity tag {opc_tag}: {e}")

                # Monitor preset name changes
                if PRESET_NAME_TAG in app_state.tags:
                    new_name = app_state.tags[PRESET_NAME_TAG]["data"].latest_value
                    prev_name = prev_preset_names.get(machine_id)
                    if prev_name is not None and new_name is not None and new_name != prev_name:
                        add_preset_log_entry(prev_name, new_name, machine_id=machine_id)
                    prev_preset_names[machine_id] = new_name

            except Exception as e:
                logger.error(f"Fatal error in section 7-2 monitoring: {e}")
                logger.exception("Full traceback:")
    
        # Create the log entries display - with even more compact styling
        log_entries = []
    
        # Determine which log to display based on mode
        display_log = machine_control_log
        if mode == "historical":
            hours = time_state.get("hours", 24) if isinstance(time_state, dict) else 24
            machine_id = active_machine_data.get("machine_id") if active_machine_data else None
            display_log = get_historical_control_log(timeframe=hours, machine_id=machine_id)
            display_log = sorted(display_log, key=lambda e: e.get("timestamp"), reverse=True)
        elif mode in LIVE_LIKE_MODES:
            # Debug logging to see what's in the control log
            #logger.debug(
            #    "Total entries in machine_control_log: %s",
            #    len(machine_control_log),
            #)
            #logger.debug("Looking for entries with machine_id=%s", machine_id)
            
            # More permissive filtering - include entries that match the machine_id
            display_log = []
            for entry in machine_control_log:
                entry_machine_id = entry.get("machine_id")
                is_demo = entry.get("demo", False)
                #logger.debug(
                #    "Entry: machine_id=%s, demo=%s, tag=%s",
                #    entry_machine_id,
                #    is_demo,
                #    entry.get('tag', 'N/A'),
                #)
                
                # Include if machine_id matches (regardless of demo flag for now)
                if str(entry_machine_id) == str(machine_id):
                    display_log.append(entry)
                    #logger.debug("Including entry: %s", entry.get('tag', 'N/A'))
            
            #logger.debug("Filtered to %s entries for machine %s", len(display_log), machine_id)
    
        # newest entries first - sort by timestamp if available
        if display_log:
            try:
                display_log = sorted(display_log, key=lambda e: e.get("timestamp", datetime.min), reverse=True)
            except Exception as e:
                logger.error(f"Error sorting display_log: {e}")
        
        display_log = display_log[:20]
    
        for idx, entry in enumerate(display_log, start=1):
            timestamp = entry.get("display_timestamp")
            if not timestamp:
                ts = entry.get("timestamp")
                if isinstance(ts, datetime):
                    timestamp = ts.strftime("%Y-%m-%d %H:%M:%S")
                elif ts:
                    timestamp = str(ts)
                else:
                    t = entry.get("time")
                    if isinstance(t, datetime):
                        timestamp = t.strftime("%Y-%m-%d %H:%M:%S")
                    elif t:
                        timestamp = str(t)
                    else:
                        timestamp = ""
    
            def _translate_tag(tag):
                if tag.startswith("Sens "):
                    parts = tag.split()
                    if len(parts) >= 2:
                        return f"{tr('sensitivity_label', lang)} {parts[1]}"
                if tag.startswith("Feeder") or tag.startswith("Feed"):
                    parts = tag.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        return tr(f"feeder_{parts[1]}", lang)
                    return tr('feeder_label', lang).rstrip(':')
                return tag
    
            tag_translated = _translate_tag(entry.get('tag', ''))
    
            icon_val = entry.get("icon")
            if icon_val in ("✅", "❌"):
                color_class = "text-success" if entry.get("action") == "Enabled" else "text-danger"
                icon = html.Span(icon_val, className=color_class)
                log_entries.append(
                    html.Div(
                        [f"{idx}. {tag_translated} {entry.get('action')} ", icon, f" {timestamp}"],
                        className="mb-1 small",
                        style={"whiteSpace": "nowrap"},
                    )
                )
            elif icon_val in ("⬆", "⬇"):
                color_class = "text-success" if icon_val == "⬆" else "text-danger"
                icon = html.Span(icon_val, className=color_class)
                value_change = f"{entry.get('old_value', '')} -> {entry.get('new_value', '')}"
                log_entries.append(
                    html.Div(
                        [f"{idx}. {tag_translated} ", icon, f" {value_change} {timestamp}"],
                        className="mb-1 small",
                        style={"whiteSpace": "nowrap"},
                    )
                )
            elif icon_val == "🔄":
                icon = html.Span(icon_val)
                log_entries.append(
                    html.Div(
                        [f"{idx}. Preset \"{entry.get('old_value', '')}\" ", icon, f" \"{entry.get('new_value', '')}\" {timestamp}"],
                        className="mb-1 small",
                        style={"whiteSpace": "nowrap"},
                    )
                )
            else:
                description = f"{tag_translated} {entry.get('action', '')}".strip()
                value_change = f"{entry.get('old_value', '')} -> {entry.get('new_value', '')}"
                log_entries.append(
                    html.Div(
                        f"{idx}. {description} {value_change} {timestamp}",
                        className="mb-1 small",
                        style={"whiteSpace": "nowrap"}
                    )
                )
    
        # If no entries, show placeholder
        if not log_entries:
            log_entries.append(
                html.Div(tr("no_changes_yet", lang), className="text-center text-muted py-1")
            )
    
        # Return the section content with title
        return html.Div(
            [html.H5(tr("machine_control_log_title", lang), className="text-left mb-1"), *log_entries],
            className="overflow-auto px-0",
            # Use flexbox so this log grows with available space
            style={"flex": "1"}
        )

    @app.callback(
        [Output("historical-time-index", "data"),
         Output("historical-time-display", "children"),
         Output("historical-data-cache", "data")],
        [Input("historical-time-slider", "value"),
         Input("mode-selector", "value")],
        [State("active-machine-store", "data")],
        prevent_initial_call=True
    )
    def update_historical_time_and_display(slider_value, mode, active_machine_data):
        """Return the chosen historical range, display text, and cached data."""
        if mode != "historical":
            return dash.no_update, "", dash.no_update
    
        # Load filtered historical data for the selected timeframe so the graphs
        # update immediately when the slider changes
        machine_id = active_machine_data.get("machine_id") if active_machine_data else None
        historical_data = load_historical_data(f"{slider_value}h", machine_id=machine_id)
    
        # Use counter 1 as the reference for the time axis.  If data exists, format
        # the first timestamp for display to indicate the starting point.
        ref_counter = 1
        timestamp_str = ""
        if (ref_counter in historical_data and
                historical_data[ref_counter]['times']):
            first_ts = historical_data[ref_counter]['times'][0]
            if isinstance(first_ts, datetime):
                timestamp_str = first_ts.strftime("%H:%M")
            else:
                timestamp_str = str(first_ts)
    
        display_text = f"Showing last {slider_value} hours"
        if timestamp_str:
            display_text += f" starting {timestamp_str}"
    
    
        # Return the selected timeframe, display text, and cached data
        return {"hours": slider_value}, display_text, historical_data

    @app.callback(
        Output("historical-time-controls", "className"),
        [Input("mode-selector", "value")],
        prevent_initial_call=True
    )
    def toggle_historical_controls_visibility(mode):
        """Show/hide historical controls based on selected mode"""
        if mode == "historical":
            return "d-block"  # Show controls
        else:
            return "d-none"  # Hide controls

    @app.callback(
        [Output("lab-test-controls", "className"),
         Output("lab-start-selector-col", "className")],
        [Input("mode-selector", "value")],
        prevent_initial_call=True,
    )
    def toggle_lab_controls_visibility(mode):
        cls = "d-flex" if mode == "lab" else "d-none"
        return cls, cls

    @app.callback(
        [Output("start-test-btn", "disabled"),
         Output("start-test-btn", "color"),
         Output("stop-test-btn", "disabled"),
         Output("stop-test-btn", "color")],
        [Input("lab-test-running", "data"),
         Input("mode-selector", "value"),
         Input("status-update-interval", "n_intervals")],
        [State("lab-test-stop-time", "data")],
    )
    def toggle_lab_test_buttons(running, mode, n_intervals, stop_time):
        """Enable/disable lab start/stop buttons based on test state."""
        print(
            f"[LAB TEST DEBUG] toggle_lab_test_buttons running={running}, "
            f"stop_time={stop_time}",
            flush=True,
        )
        if mode != "lab":
            return True, "secondary", True, "secondary"

        # Disable both buttons during the 30s grace period after stopping
        if running and stop_time and (time.time() - abs(stop_time) < 30):
            print("[LAB TEST DEBUG] grace period active - buttons disabled", flush=True)
            return True, "secondary", True, "secondary"

        if running:
            return True, "secondary", False, "danger"

        return False, "success", True, "secondary"

    @app.callback(
        Output("lab-test-running", "data"),
        [Input("start-test-btn", "n_clicks"),
         Input("stop-test-btn", "n_clicks"),
         Input("mode-selector", "value"),
         Input("status-update-interval", "n_intervals")],
        [State("lab-test-running", "data"),
         State("lab-test-stop-time", "data"),
         State("lab-test-name", "value"),
         State("lab-start-selector", "value")],
        prevent_initial_call=True,
    )

    def update_lab_running(start_click, stop_click, mode, n_intervals, running, stop_time, test_name, start_mode):
        """Update lab running state based on start/stop actions or feeder status."""
        global current_lab_filename
        ctx = callback_context
        triggers = [t["prop_id"].split(".")[0] for t in ctx.triggered] if ctx.triggered else []
        trigger = "interval"
        if "stop-test-btn" in triggers:
            trigger = "stop-test-btn"
        elif "start-test-btn" in triggers:
            trigger = "start-test-btn"
        elif triggers:
            trigger = triggers[0]
        print(
            f"[LAB TEST DEBUG] update_lab_running triggers={triggers} selected={trigger} running={running}, stop_time={stop_time}",


            flush=True,
        )

        if mode != "lab":
            return False

        if ctx.triggered:
            if start_mode != "feeder":
                if trigger == "start-test-btn":
                    print("[LAB TEST] Start button pressed", flush=True)
                    print("[LAB TEST] Active threads:", [t.name for t in threading.enumerate()], flush=True)
                    try:
                        if active_machine_id is not None:
                            _reset_lab_session(active_machine_id)
                    except Exception as exc:
                        logger.warning(f"Failed to reset lab session: {exc}")
                    return True
                elif trigger == "stop-test-btn":
                    # Do not end the test immediately; allow a 30s grace period
                    # so logging can continue before finalizing.
                    print("[LAB TEST] Stop button pressed - entering grace period", flush=True)
                    print("[LAB TEST] Active threads:", [t.name for t in threading.enumerate()], flush=True)
                    return True

        feeders_running = False
        if (
            active_machine_id is not None
            and active_machine_id in machine_connections
        ):
            tags = machine_connections[active_machine_id].get("tags", {})
            for i in range(1, 5):
                tag = f"Status.Feeders.{i}IsRunning"
                if bool(tags.get(tag, {}).get("data", {}).latest_value if tag in tags else False):
                    feeders_running = True
                    break

        if start_mode == "feeder" and feeders_running and not running:

            print("[LAB TEST] Auto-starting test because feeders are running", flush=True)

            try:
                if active_machine_id is not None:
                    if not current_lab_filename:
                        name = test_name or "Test"
                        current_lab_filename = (
                            f"Lab_Test_{name}_{datetime.now().strftime('%m_%d_%Y_%H_%M_%S')}.csv"
                        )
                        _create_empty_lab_log(active_machine_id, current_lab_filename)
                    _reset_lab_session(active_machine_id)
            except Exception as exc:
                logger.warning(f"Failed to prepare auto lab log: {exc}")
            return True

        print(f"[LAB TEST DEBUG] running={running}, stop_time={stop_time}", flush=True)
        # Check if we should end the test based on the stop time
        if running and stop_time and (time.time() - abs(stop_time) >= 30):
            print("[LAB TEST] Grace period complete - stopping test", flush=True)
            current_lab_filename = None
            try:
                refresh_lab_cache(active_machine_id)
            except Exception as exc:
                logger.warning(f"Failed to refresh lab cache: {exc}")
            return False

        return running

    @app.callback(
        Output("lab-test-info", "data"),
        [Input("start-test-btn", "n_clicks"), Input("stop-test-btn", "n_clicks")],
        [State("lab-test-name", "value")],
        prevent_initial_call=True,
    )
    def manage_lab_test_info(start_click, stop_click, name):
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate
        trigger = ctx.triggered[0]["prop_id"].split(".")[0]
        global current_lab_filename
        if trigger == "start-test-btn":
            test_name = name or "Test"
            filename = (
                f"Lab_Test_{test_name}_{datetime.now().strftime('%m_%d_%Y')}.csv"
            )
            current_lab_filename = filename
            try:
                if active_machine_id is not None:
                    _create_empty_lab_log(active_machine_id, filename)
                    _reset_lab_session(active_machine_id)
            except Exception as exc:
                logger.warning(f"Failed to prepare new lab log: {exc}")
            return {"filename": filename}
        return {}

    @app.callback(
        [Output("metric-logging-interval", "interval"), Output("metric-logging-interval", "disabled")],
        [Input("lab-test-running", "data"), Input("mode-selector", "value")],
    )
    def adjust_logging_interval(running, mode):
        if mode == "lab":
            return 1000, not running
        return 60000, False

    @app.callback(
        Output("lab-test-stop-time", "data"),
        [Input("start-test-btn", "n_clicks"),
         Input("stop-test-btn", "n_clicks"),
         Input("status-update-interval", "n_intervals")],
        [State("lab-test-running", "data"),
         State("lab-test-stop-time", "data"),
         State("app-mode", "data"),
         State("active-machine-store", "data"),
         State("lab-start-selector", "value")],
        prevent_initial_call=True,
    )
    def update_lab_test_stop_time(start_click, stop_click, n_intervals, running, stop_time, mode, active_machine_data, start_mode):
        ctx = callback_context

        triggers = [t["prop_id"].split(".")[0] for t in ctx.triggered] if ctx.triggered else []
        trigger = "interval"
        if "stop-test-btn" in triggers:
            trigger = "stop-test-btn"
        elif "start-test-btn" in triggers:
            trigger = "start-test-btn"
        elif triggers:
            trigger = triggers[0]
        print(
            f"[LAB TEST DEBUG] update_lab_test_stop_time triggers={triggers} selected={trigger} running={running}, stop_time={stop_time}",


            flush=True,
        )

        if ctx.triggered:
            if trigger == "stop-test-btn":
                new_time = -time.time()
                print("[LAB TEST] Grace period timer started", flush=True)
                print(f"[LAB TEST DEBUG] storing stop_time={new_time}", flush=True)
                return new_time
            if trigger == "start-test-btn":
                print("[LAB TEST] Grace period cleared due to start", flush=True)
                print("[LAB TEST DEBUG] clearing stop_time", flush=True)
                return None

        if not running:
            print("[LAB TEST DEBUG] not running - stop_time unchanged", flush=True)
            return dash.no_update

        if not mode or mode.get("mode") != "lab":
            print(
                f"[LAB TEST DEBUG] not in lab mode ({mode}) - stop_time unchanged",
                flush=True,
            )
            return dash.no_update

        active_id = active_machine_data.get("machine_id") if active_machine_data else None
        if not active_id or active_id not in machine_connections:
            print(
                f"[LAB TEST DEBUG] invalid active machine {active_id} - stop_time unchanged",
                flush=True,
            )
            return dash.no_update

        tags = machine_connections[active_id].get("tags", {})
        any_running = False
        for i in range(1, 5):
            tag = f"Status.Feeders.{i}IsRunning"
            if bool(tags.get(tag, {}).get("data", {}).latest_value if tag in tags else False):
                any_running = True
                break

        if any_running:
            if stop_time is not None and stop_time >= 0:
                print("[LAB TEST DEBUG] feeders running - clearing stop time", flush=True)
                return None
        else:
            if start_mode == "feeder" and stop_time is None:
                new_time = time.time()
                print("[LAB TEST] Feeders stopped - starting grace period", flush=True)
                print(f"[LAB TEST DEBUG] storing stop_time={new_time}", flush=True)
                return new_time

        print("[LAB TEST DEBUG] no update to stop_time", flush=True)
        return dash.no_update

    @app.callback(
        [Output("display-modal", "is_open"),
         Output("display-form-container", "children")],
        [Input({"type": "open-display", "index": ALL}, "n_clicks"),
         Input("close-display-settings", "n_clicks"),
         Input("save-display-settings", "n_clicks"),
         Input("language-preference-store", "data")],
        [State("display-modal", "is_open"),
         State({"type": "display-enabled", "index": ALL}, "value")],
        prevent_initial_call=True
    )
    def toggle_display_modal(open_clicks, close_clicks, save_clicks, lang, is_open, display_enabled_values):
        """Handle opening/closing the display settings modal and saving settings"""
        global display_settings
        
        ctx = callback_context
        
        # Check if callback was triggered
        if not ctx.triggered:
            return no_update, no_update
        
        # Get the property that triggered the callback
        trigger_prop_id = ctx.triggered[0]["prop_id"]
        
        # Check for open button clicks (with pattern matching)
        if '"type":"open-display"' in trigger_prop_id:
            # Check if any button was actually clicked (not initial state)
            if any(click is not None for click in open_clicks):
                return True, create_display_settings_form(lang)
        
        # Check for close button click
        elif trigger_prop_id == "close-display-settings.n_clicks":
            # Check if button was actually clicked (not initial state)
            if close_clicks is not None:
                return False, no_update
        
        # Check for save button click
        elif trigger_prop_id == "save-display-settings.n_clicks":
            # Check if button was actually clicked (not initial state)
            if save_clicks is not None and display_enabled_values:
                # Safety check: make sure we have the right number of values
                if len(display_enabled_values) == 12:  # We expect 12 counters
                    # Update the display settings
                    for i in range(len(display_enabled_values)):
                        counter_num = i + 1
                        display_settings[counter_num] = display_enabled_values[i]
                    
                    # Save settings to file
                    save_success = save_display_settings(display_settings)
                    if save_success:
                        logger.info("Display settings saved successfully")
                    else:
                        logger.warning("Failed to save display settings")
                else:
                    logger.warning(f"Unexpected number of display values: {len(display_enabled_values)}")
                
                # Close modal
                return False, create_display_settings_form(lang)
        
        # Default case - don't update anything
        return no_update, no_update

    @app.callback(
        [Output("production-rate-units-modal", "is_open"),
         Output("production-rate-unit", "data")],
        [Input({"type": "open-production-rate-units", "index": ALL}, "n_clicks"),
         Input("close-production-rate-units", "n_clicks"),
         Input("save-production-rate-units", "n_clicks")],
        [State("production-rate-units-modal", "is_open"),
         State("production-rate-unit-selector", "value")],
        prevent_initial_call=True,
    )
    def toggle_production_rate_units_modal(open_clicks, close_clicks, save_clicks, is_open, selected):
        """Show or hide the units selection modal and save the chosen unit."""
        ctx = callback_context
        if not ctx.triggered:
            return no_update, no_update
    
        trigger = ctx.triggered[0]["prop_id"]
        if '"type":"open-production-rate-units"' in trigger:
            if any(click is not None for click in open_clicks):
                return True, dash.no_update
        elif trigger == "close-production-rate-units.n_clicks":
            if close_clicks is not None:
                return False, dash.no_update
        elif trigger == "save-production-rate-units.n_clicks":
            if save_clicks is not None:
                return False, selected
    
        return no_update, no_update

    @app.callback(
        [Output("additional-image-store", "data"),
         Output("upload-status", "children"),
         Output("image-error-store", "data")],
        [Input("upload-image", "contents")],
        [State("upload-image", "filename")]
    )
    def handle_image_upload_enhanced(contents, filename):
        """Validate, cache, and store uploaded image."""
        if contents is None:
            return dash.no_update, dash.no_update, None

        logger.info(f"Processing image upload: {filename}")
        processed, err = img_utils.validate_and_process_image(contents)
        if err:
            logger.error(f"Image validation failed: {err}")
            return dash.no_update, html.Div(f"Error uploading image: {err}", className="text-danger"), err

        success, err = img_utils.cache_image(processed)
        if not success:
            logger.error(f"Error caching image: {err}")
            return dash.no_update, html.Div(f"Error uploading image: {err}", className="text-danger"), err

        new_data = {"image": processed}
        return new_data, html.Div(f"Uploaded: {filename}", className="text-success"), None

    @app.callback(
        [Output("image-error-alert", "children"),
         Output("image-error-alert", "is_open")],
        Input("image-error-store", "data"),
        prevent_initial_call=True,
    )
    def show_image_errors(msg):
        if msg:
            return msg, True
        return "", False

    @app.callback(
        Output("update-counts-modal", "is_open"),
        [Input("open-update-counts", "n_clicks"),
         Input("close-update-counts", "n_clicks"),
         Input("save-count-settings", "n_clicks")],
        [State("update-counts-modal", "is_open")],
        prevent_initial_call=True,
    )
    def toggle_update_counts_modal(open_click, close_click, save_click, is_open):
        ctx = callback_context
        if not ctx.triggered:
            return dash.no_update
    
        trigger = ctx.triggered[0]["prop_id"]
        if trigger == "open-update-counts.n_clicks" and open_click:
            return True
        elif trigger == "close-update-counts.n_clicks" and close_click:
            return False
        elif trigger == "save-count-settings.n_clicks" and save_click:
            return False
    
        return is_open

    @app.callback(
        [Output("app-mode", "data"),
         Output("historical-time-slider", "value")],
        [Input("mode-selector", "value")],
        prevent_initial_call=False
    )
    def update_app_mode(mode):
        """Update the application mode (live, demo, or historical)"""
        # Reset historical slider to most recent when switching to historical mode
        slider_value = 24 if mode == "historical" else dash.no_update
    
        # Log the new mode for debugging unexpected switches
        #logger.info(f"App mode updated to '{mode}'")
    
        return {"mode": mode}, slider_value

    @app.callback(Output("app-mode-tracker", "data"), Input("app-mode", "data"))
    def _track_app_mode(data):
        """Synchronize ``current_app_mode`` with the ``app-mode`` store."""
        from EnpresorOPCDataViewBeforeRestructureLegacy import (
            current_app_mode,
            set_current_app_mode,
        )

        if isinstance(data, dict) and "mode" in data:
            new_mode = data["mode"]
            if new_mode != current_app_mode:
                set_current_app_mode(new_mode)
                if new_mode == "lab":
                    print("[LAB TEST] Lab mode activated - pausing background threads", flush=True)
                    pause_background_processes()
                else:
                    print("[LAB TEST] Exiting lab mode - resuming background threads", flush=True)
                    resume_background_processes()
        return dash.no_update

    @app.callback(
        [Output("threshold-modal", "is_open")],  # Changed this to remove the second output
        [Input({"type": "open-threshold", "index": ALL}, "n_clicks"),
         Input("close-threshold-settings", "n_clicks"),
         Input("save-threshold-settings", "n_clicks")],
        [State("threshold-modal", "is_open"),
         State({"type": "threshold-min-enabled", "index": ALL}, "value"),
         State({"type": "threshold-max-enabled", "index": ALL}, "value"),
         State({"type": "threshold-min-value", "index": ALL}, "value"),
         State({"type": "threshold-max-value", "index": ALL}, "value"),
         State("threshold-email-address", "value"),
         State("threshold-email-minutes", "value"),
         State("threshold-email-enabled", "value"),
         State("counter-view-mode", "data")],
        prevent_initial_call=True
    )
    def toggle_threshold_modal(open_clicks, close_clicks, save_clicks, is_open,
                              min_enabled_values, max_enabled_values, min_values, max_values,
                              email_address, email_minutes, email_enabled, mode):
        """Handle opening/closing the threshold settings modal and saving settings"""
        global threshold_settings
        
        ctx = callback_context
        
        # Check if callback was triggered
        if not ctx.triggered:
            return [no_update]  # Return as a list with one element
        
        # Get the property that triggered the callback
        trigger_prop_id = ctx.triggered[0]["prop_id"]
        
        # Check for open button clicks (with pattern matching)
        if '"type":"open-threshold"' in trigger_prop_id:
            # Check if any button was actually clicked (not initial state)
            if any(click is not None for click in open_clicks):
                return [True]  # Return as a list with one element
        
        # Check for close button click
        elif trigger_prop_id == "close-threshold-settings.n_clicks":
            # Check if button was actually clicked (not initial state)
            if close_clicks is not None:
                return [False]  # Return as a list with one element
        
        # Check for save button click
        elif trigger_prop_id == "save-threshold-settings.n_clicks":
            # Check if button was actually clicked (not initial state)
            if save_clicks is not None and min_enabled_values:
                # Update the threshold settings
                for i in range(len(min_enabled_values)):
                    counter_num = i + 1
                    threshold_settings[counter_num] = {
                        'min_enabled': min_enabled_values[i],
                        'max_enabled': max_enabled_values[i],
                        'min_value': float(min_values[i]),
                        'max_value': float(max_values[i])
                    }
                
                # Save the email settings
                threshold_settings['email_enabled'] = email_enabled
                threshold_settings['email_address'] = email_address
                threshold_settings['email_minutes'] = int(email_minutes) if email_minutes is not None else 2
                threshold_settings['counter_mode'] = mode
                
                # Save settings to file
                save_success = save_threshold_settings(threshold_settings)
                if save_success:
                    logger.info("Threshold settings saved successfully")
                else:
                    logger.warning("Failed to save threshold settings")
                
                # Close modal - no need to update the settings display anymore
                return [False]  # Return as a list with one element
        
        # Default case - don't update anything
        return [no_update]  # Return as a list with one element

    @app.callback(
        Output("threshold-form-container", "children"),
        [Input({"type": "open-threshold", "index": ALL}, "n_clicks"),
         Input("language-preference-store", "data"),
         Input("counter-view-mode", "data")],
        prevent_initial_call=True,
    )
    def refresh_threshold_form(open_clicks, lang, mode):
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate

        trigger = ctx.triggered[0]["prop_id"]
        if '"type":"open-threshold"' in trigger:
            if any(click is not None for click in open_clicks):
                return create_threshold_settings_form(lang, mode)
        if trigger == "language-preference-store.data" or trigger == "counter-view-mode.data":
            return create_threshold_settings_form(lang, mode)
        raise PreventUpdate

    @app.callback(
        [Output({"type": "threshold-min-value", "index": ALL}, "value"),
         Output({"type": "threshold-max-value", "index": ALL}, "value")],
        Input("auto-set-button", "n_clicks"),
        State("auto-set-percent", "value"),
        State("counter-view-mode", "data"),
        prevent_initial_call=True,
    )
    def auto_set_thresholds(n_clicks, percent, mode):
        if not n_clicks:
            raise PreventUpdate

        tolerance = (percent or 20) / 100.0
        global previous_counter_values, threshold_settings

        if mode == "percent":
            total_val = sum(previous_counter_values)
            current_values = [
                (v / total_val * 100) if total_val else 0
                for v in previous_counter_values
            ]
        else:
            current_values = previous_counter_values

        new_mins = []
        new_maxs = []
        for i, value in enumerate(current_values):
            min_val = round(value * (1 - tolerance), 2)
            max_val = round(value * (1 + tolerance), 2)
            new_mins.append(min_val)
            new_maxs.append(max_val)

            counter_num = i + 1
            if counter_num in threshold_settings:
                threshold_settings[counter_num]['min_value'] = min_val
                threshold_settings[counter_num]['max_value'] = max_val

        return new_mins, new_maxs

    @app.callback(
        Output("counter-view-mode", "data"),
        Input("counter-mode-toggle", "value"),
        prevent_initial_call=True,
    )
    def set_counter_view_mode(value):
        """Store the user's preferred counter display mode."""
        global threshold_settings
        if isinstance(threshold_settings, dict):
            threshold_settings["counter_mode"] = value
        return value

    @app.callback(
        Output("metric-logging-store", "data"),
        [Input("metric-logging-interval", "n_intervals")],
    
        [State("app-state", "data"),
         State("app-mode", "data"),
         State("machines-data", "data"),
         State("production-data-store", "data"),
         State("weight-preference-store", "data"),
         State("lab-test-running", "data"),
         State("active-machine-store", "data"),
         State("lab-test-info", "data")],
        prevent_initial_call=True,
    )
    def log_current_metrics(n_intervals, app_state_data, app_mode, machines_data, production_data, weight_pref, lab_running, active_machine_data, lab_test_info):

        """Collect metrics for each connected machine and append to its file.

        In lab mode, metrics are logged at every interval.
        """
        global machine_connections, current_lab_filename
    
        CAPACITY_TAG = "Status.ColorSort.Sort1.Throughput.KgPerHour.Current"
        REJECTS_TAG = "Status.ColorSort.Sort1.Total.Percentage.Current"
        OPM_TAG = "Status.ColorSort.Sort1.Throughput.ObjectPerMin.Current"
        OPM_60M_TAG = "Status.ColorSort.Sort1.Throughput.ObjectPerMin.60M"
        COUNTER_TAG = "Status.ColorSort.Sort1.DefectCount{}.Rate.60M"
        mode = "demo"
        if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
            mode = app_mode["mode"]
    
        if not weight_pref:
            weight_pref = load_weight_preference()
    
        if mode == "demo":
            if machines_data and machines_data.get("machines"):
                for m in machines_data["machines"]:
                    prod = (m.get("operational_data") or {}).get("production", {})
                    capacity = prod.get("capacity", 0)
                    accepts = prod.get("accepts", 0)
                    rejects = prod.get("rejects", 0)
    
                    metrics = {
                        "capacity": convert_capacity_to_lbs(capacity, weight_pref),
                        "accepts": convert_capacity_to_lbs(accepts, weight_pref),
                        "rejects": convert_capacity_to_lbs(rejects, weight_pref),
                        "objects_per_min": 0,
                        "objects_60M": 0,
                        "running": 1,
                        "stopped": 0,
                    }
    
                    counters = m.get("demo_counters", [0] * 12)
                    for i in range(1, 13):
                        metrics[f"counter_{i}"] = counters[i-1] if i-1 < len(counters) else 0
    
                    append_metrics(metrics, machine_id=str(m.get("id")), mode="Demo")
    
            return dash.no_update

        if mode == "lab" and not lab_running:
            return dash.no_update

        if mode == "lab":
            active_machine_id = (
                active_machine_data.get("machine_id") if active_machine_data else None
            )
            if not active_machine_id or active_machine_id not in machine_connections:
                return dash.no_update
            machines_iter = {active_machine_id: machine_connections[active_machine_id]}.items()
            lab_filename = None
            if isinstance(lab_test_info, dict):
                lab_filename = lab_test_info.get("filename")
            if not lab_filename:
                lab_filename = current_lab_filename

            # If no filename is available yet, skip logging rather than
            # creating a generic file.  This avoids race conditions where a
            # log entry could be written to ``Lab_Test_<date>.csv`` just after
            # a test stops.
            if not lab_filename:
                return dash.no_update

            current_lab_filename = lab_filename
        else:
            machines_iter = machine_connections.items()

        for machine_id, info in machines_iter:
            if not info.get("connected", False):
                continue
            tags = info["tags"]
            capacity_value = tags.get(CAPACITY_TAG, {}).get("data").latest_value if CAPACITY_TAG in tags else None

            capacity_lbs = capacity_value * 2.205 if capacity_value is not None else 0

            opm = tags.get(OPM_TAG, {}).get("data").latest_value if OPM_TAG in tags else 0
            opm60 = tags.get(OPM_60M_TAG, {}).get("data").latest_value if OPM_60M_TAG in tags else 0
            if opm is None:
                opm = 0
            if opm60 is None:
                opm60 = 0

            reject_count = 0
            counters = {}
            for i in range(1, 13):
                tname = COUNTER_TAG.format(i)
                val = tags.get(tname, {}).get("data").latest_value if tname in tags else 0
                if val is None:
                    val = 0
                counters[f"counter_{i}"] = val
                reject_count += val

            reject_pct = (reject_count / opm) if opm else 0
            rejects_lbs = capacity_lbs * reject_pct
            accepts_lbs = capacity_lbs - rejects_lbs
    
            # Determine feeder running state
            feeder_running = False
            for i in range(1, 5):
                run_tag = f"Status.Feeders.{i}IsRunning"
                if run_tag in tags:
                    val = tags[run_tag]["data"].latest_value
                    if bool(val):
                        feeder_running = True
                        break

            metrics = {
                "capacity": capacity_lbs,
                "accepts": accepts_lbs,
                "rejects": rejects_lbs,
                "objects_per_min": opm,
                "objects_60M": opm60,
                "running": 1 if feeder_running else 0,
                "stopped": 0 if feeder_running else 1,
            }
            metrics.update(counters)

            log_mode = "Lab" if mode == "lab" else "Live"
            if mode == "lab":
                # Clamp negative or extremely small values when logging lab data
                for key, value in metrics.items():
                    if isinstance(value, (int, float)):
                        if value < 0 or abs(value) < SMALL_VALUE_THRESHOLD:
                            metrics[key] = 0
                append_metrics(
                    metrics,
                    machine_id=str(machine_id),
                    filename=lab_filename,
                    mode=log_mode,
                )
            else:
                append_metrics(metrics, machine_id=str(machine_id), mode=log_mode)
    
        return dash.no_update
