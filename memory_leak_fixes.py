import threading
import time
import gc
from typing import Any


class MemoryMonitor:
    """Simple periodic garbage collector trigger."""

    def __init__(self, interval: float = 30.0) -> None:
        self.interval = interval
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._running = False

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._thread.start()

    def _run(self) -> None:
        while self._running:
            gc.collect()
            time.sleep(self.interval)

    def stop(self) -> None:
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1)


class CounterHistoryManager:
    """Track and reset counter history."""

    def __init__(self) -> None:
        self.history = []
        self.lock = threading.Lock()

    def add(self, value: Any) -> None:
        with self.lock:
            self.history.append(value)

    def clear(self) -> None:
        with self.lock:
            self.history.clear()


class ImageManager:
    """Manage cached images to avoid memory leaks."""

    def __init__(self) -> None:
        self.cache = {}
        self.lock = threading.Lock()

    def cache_image(self, key: str, data: Any) -> None:
        with self.lock:
            self.cache[key] = data

    def purge(self) -> None:
        with self.lock:
            self.cache.clear()


class DataFrameProcessor:
    """Utility for pruning large DataFrames."""

    def prune(self, df, max_rows: int = 1000):
        if getattr(df, "shape", (0,))[0] > max_rows:
            return df.tail(max_rows)
        return df


class AppStateManager:
    """Periodically clean up data stored in ``AppState``."""

    def __init__(self, app_state, interval: float = 60.0) -> None:
        self.app_state = app_state
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start_cleanup_thread(self) -> None:
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(self.interval)
            self.cleanup()

    def cleanup(self) -> None:
        tags = getattr(self.app_state, "tags", {})
        for tag in tags.values():
            # prune history if TagData-like
            max_points = getattr(tag, "max_points", None)
            if max_points is not None and hasattr(tag, "timestamps") and hasattr(tag, "values"):
                if len(tag.timestamps) > max_points:
                    tag.timestamps = tag.timestamps[-max_points:]
                    tag.values = tag.values[-max_points:]

    def stop_cleanup_thread(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1)


__all__ = [
    "MemoryMonitor",
    "CounterHistoryManager",
    "ImageManager",
    "DataFrameProcessor",
    "AppStateManager",
]
