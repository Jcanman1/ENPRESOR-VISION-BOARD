import pandas as pd
import logging
import gc

logger = logging.getLogger(__name__)

def safe_read_csv(path, *args, **kwargs):
    """Read a CSV file and return a DataFrame.

    Any exceptions are logged and an empty ``DataFrame`` is returned.
    """
    try:
        return pd.read_csv(path, *args, **kwargs)
    except Exception as exc:
        logger.error(f"Failed to read CSV {path}: {exc}")
        return pd.DataFrame()

def process_with_cleanup(data, func, *args, **kwargs):
    """Run ``func`` on ``data`` and attempt to free memory afterwards."""
    result = func(data, *args, **kwargs)
    try:
        del data
        gc.collect()
    except Exception:
        pass
    return result
