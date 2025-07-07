"""Simple helpers for monitoring process memory consumption."""

import logging
import os

logger = logging.getLogger(__name__)


def _get_process_memory_mb():
    """Return RSS memory for the current process in MB."""
    try:
        import psutil

        process = psutil.Process(os.getpid())
        return process.memory_info().rss / 1_048_576
    except Exception:
        try:
            import resource

            # ru_maxrss is kilobytes on Linux
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except Exception:
            return 0.0


def log_memory_if_high(threshold_mb: float = 100.0) -> None:
    """Log a warning if process memory exceeds the given threshold."""
    mem = _get_process_memory_mb()
    if mem > threshold_mb:
        logger.warning("High memory usage detected: %.1f MB", mem)

