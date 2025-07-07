"""Helper functions for safely processing large CSV files."""

import pandas as pd
import logging
import gc

logger = logging.getLogger(__name__)

def safe_read_csv(path, *args, **kwargs):
    """Read a CSV file into a ``DataFrame`` with error handling.

    Any parse issues are logged and an empty frame is returned so callers
    can gracefully handle missing or corrupt files.
    """
    try:
        return pd.read_csv(path, *args, **kwargs)
    except Exception as exc:
        logger.error(f"Failed to read CSV {path}: {exc}")
        return pd.DataFrame()

def process_with_cleanup(data, func, *args, **kwargs):
    """Run ``func`` on ``data`` and explicitly free memory afterwards."""
    result = func(data, *args, **kwargs)
    try:
        del data
        gc.collect()
    except Exception:
        pass
    return result
