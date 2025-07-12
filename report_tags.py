import os
import json
import logging

from hourly_data_saving import EXPORT_DIR as METRIC_EXPORT_DIR

logger = logging.getLogger(__name__)

REPORT_SETTINGS_TAGS = {
    "Settings.Ejectors.PrimaryDelay",
    "Settings.Ejectors.PrimaryDwell",
    "Settings.Ejectors.PixelOverlap",
    "Settings.Calibration.NonObjectBand",
    "Settings.ColorSort.Config.Erosion",
    "Settings.Calibration.LedDriveForGain",
    "Settings.Calibration.FrontProductRed",
    "Settings.Calibration.FrontProductGreen",
    "Settings.Calibration.FrontProductBlue",
    "Settings.Calibration.FrontBackgroundRed",
    "Settings.Calibration.FrontBackgroundGreen",
    "Settings.Calibration.FrontBackgroundBlue",
    # Sensitivity specific tags for PDF reports
    *{
        f"Settings.ColorSort.Primary{i}.FrontAndRearLogic" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.SampleImage" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.Name" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.EllipsoidCenterX" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.EllipsoidCenterY" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.EllipsoidCenterZ" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.EjectorDelayOffset" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.Sensitivity" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.EllipsoidAxisLengthX" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.EllipsoidAxisLengthY" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.EllipsoidAxisLengthZ" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.EjectorDwellOffset" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.TypeId" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.EllipsoidRotationX" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.EllipsoidRotationY" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.EllipsoidRotationZ" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.AreaSize" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.IsActive" for i in range(1, 13)
    },
    *{
        f"Settings.ColorSort.Primary{i}.IsAssigned" for i in range(1, 13)
    },
}


import re


def _primary_num(name: str) -> int | None:
    """Return the primary number encoded in a tag name or ``None``."""
    m = re.search(r"Primary(\d+)", name)
    if m:
        try:
            return int(m.group(1))
        except Exception:  # pragma: no cover - regex group not numeric
            return None
    return None


def save_machine_settings(machine_id, machine_connections, export_dir=METRIC_EXPORT_DIR, *, active_only=False):
    """Save current REPORT_SETTINGS_TAGS values for a machine.

    If ``active_only`` is ``True``, sensitivity specific tags (except the
    ``IsActive`` and ``IsAssigned`` flags) are only saved for sensitivities that
    are currently active according to the OPC tags.
    """

    info = machine_connections.get(str(machine_id)) or machine_connections.get(machine_id)
    if not info or "tags" not in info:
        return None

    tags = info["tags"]

    active_set = set(range(1, 13))
    if active_only:
        active_set.clear()
        for i in range(1, 13):
            flag_name = f"Settings.ColorSort.Primary{i}.IsAssigned"
            tag = tags.get(flag_name)
            if not tag:
                continue
            try:
                val = tag["node"].get_value()
            except Exception:
                val = getattr(tag["data"], "latest_value", None)
            if bool(val):
                active_set.add(i)

    settings = {}
    for name in REPORT_SETTINGS_TAGS:
        num = _primary_num(name)
        if (
            active_only
            and num is not None
            and name.endswith((".IsActive", ".IsAssigned")) is False
            and num not in active_set
        ):
            continue

        tag = tags.get(name)
        if not tag:
            continue
        try:
            value = tag["node"].get_value()
        except Exception:
            value = getattr(tag["data"], "latest_value", None)
        settings[name] = value

    machine_dir = os.path.join(export_dir, str(machine_id))
    os.makedirs(machine_dir, exist_ok=True)
    path = os.path.join(machine_dir, "settings.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception as exc:  # pragma: no cover - disk issues
        logger.warning(f"Unable to save machine settings for {machine_id}: {exc}")
        return None
    return path
