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


def save_machine_settings(machine_id, machine_connections, export_dir=METRIC_EXPORT_DIR):
    """Save current REPORT_SETTINGS_TAGS values for a machine."""
    info = machine_connections.get(str(machine_id)) or machine_connections.get(machine_id)
    if not info or "tags" not in info:
        return None

    tags = info["tags"]
    settings = {}
    for name in REPORT_SETTINGS_TAGS:
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
