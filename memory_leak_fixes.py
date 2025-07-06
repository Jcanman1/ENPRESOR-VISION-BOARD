"""Utility classes for monitoring and reducing memory usage."""

import logging
import os
import gc
import threading
import io
from typing import Any, Dict, List

import psutil
from PIL import Image
import pandas as pd


class MemoryMonitor:
    """Monitor and log memory usage for the current process."""

    def __init__(self, threshold_mb: float) -> None:
        self.threshold_mb = threshold_mb
        self.process = psutil.Process()
        self.logger = logging.getLogger(__name__)

    def get_memory_usage(self) -> float:
        """Return memory usage of the process in MB."""
        mem_bytes = self.process.memory_info().rss
        return mem_bytes / (1024 * 1024)

    def log_memory_if_high(self) -> None:
        """Log a warning if memory usage exceeds the threshold."""
        usage = self.get_memory_usage()
        if usage > self.threshold_mb:
            self.logger.warning("High memory usage: %.2f MB", usage)


class CounterHistoryManager:
    """Manage a fixed-size list of numeric history points."""

    def __init__(self, max_points: int) -> None:
        self.max_points = max_points
        self.history: List[float] = []

    def add_point(self, value: float) -> None:
        """Add a new point and trim history to ``max_points``."""
        self.history.append(value)
        if len(self.history) > self.max_points:
            self.history = self.history[-self.max_points:]


class ImageManager:
    """Validate image size and optionally compress before caching."""

    def __init__(self, max_size_mb: float) -> None:
        self.max_size_mb = max_size_mb

    def validate_and_compress(self, image_bytes: bytes) -> bytes:
        """Return compressed image bytes if size exceeds ``max_size_mb``."""
        size_mb = len(image_bytes) / (1024 * 1024)
        if size_mb <= self.max_size_mb:
            return image_bytes
        with Image.open(io.BytesIO(image_bytes)) as img:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", optimize=True, quality=80)
            return buf.getvalue()

    def cache_image(self, image_bytes: bytes, cache: Dict[str, bytes], key: str) -> None:
        """Store the (possibly compressed) image in ``cache`` under ``key``."""
        cache[key] = self.validate_and_compress(image_bytes)


class DataFrameProcessor:
    """Safely load CSV files and provide clean-up utilities."""

    def __init__(self, max_file_size_mb: float) -> None:
        self.max_file_size_mb = max_file_size_mb

    def read_csv_safe(self, path: str) -> pd.DataFrame:
        """Read a CSV if it does not exceed ``max_file_size_mb``."""
        size_mb = os.path.getsize(path) / (1024 * 1024)
        if size_mb > self.max_file_size_mb:
            raise ValueError("File too large: %.2f MB" % size_mb)
        df = pd.read_csv(path)
        self._cleanup(df)
        return df

    def _cleanup(self, df: pd.DataFrame) -> None:
        """Drop duplicates and release memory."""
        df.drop_duplicates(inplace=True)
        gc.collect()


class AppStateManager:
    """Periodically clean entries from a shared application state."""

    def __init__(self, app_state: Dict[str, Any]) -> None:
        self.app_state = app_state
        self._cleanup_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start_periodic_cleanup(self, interval_sec: int = 300) -> None:
        """Start a daemon thread that calls ``clean_state`` every interval."""
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            return
        self._stop_event.clear()
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, args=(interval_sec,), daemon=True
        )
        self._cleanup_thread.start()

    def stop(self) -> None:
        """Stop the background cleanup thread."""
        self._stop_event.set()
        if self._cleanup_thread:
            self._cleanup_thread.join()

    def clean_state(self) -> None:
        """Remove keys with ``None`` values and run garbage collection."""
        for key in list(self.app_state.keys()):
            if self.app_state[key] is None:
                del self.app_state[key]
        gc.collect()

    def _cleanup_loop(self, interval_sec: int) -> None:
        while not self._stop_event.is_set():
            try:
                self.clean_state()
            finally:
                self._stop_event.wait(interval_sec)
