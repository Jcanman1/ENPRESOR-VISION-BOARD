"""Utilities for managing counter history data."""

from typing import Dict, Any

MAX_POINTS = 120


def add_data_point(history: Dict[int, Dict[str, list]], counter_num: int,
                   timestamp, value, max_points: int = MAX_POINTS) -> None:
    """Append a timestamp/value pair, trimming to ``max_points``."""
    data = history.setdefault(counter_num, {"times": [], "values": []})
    data["times"].append(timestamp)
    data["values"].append(value)
    if len(data["times"]) > max_points:
        data["times"] = data["times"][-max_points:]
        data["values"] = data["values"][-max_points:]

