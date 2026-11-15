"""Background worker infrastructure for periodic Watcher scans.

The transaction listing and case handoff interfaces are not present yet. A
caller supplies the scan operation once those integrations are agreed; this
module owns only its periodic execution and lifecycle.
"""

import logging
import math
import os
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


def _configured_interval() -> float:
    raw_interval = os.getenv("WATCHER_INTERVAL", "10")
    try:
        interval = float(raw_interval)
    except ValueError as exc:
        raise ValueError("WATCHER_INTERVAL must be a positive number of seconds") from exc
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("WATCHER_INTERVAL must be a positive number of seconds")
    return interval


class Watcher:
    """Run an injected scan operation periodically on a stoppable thread.

    ``scan`` is the integration seam for listing failed transactions and
    handing them to the Brain workflow. It is deliberately not bound to an
    assumed Body tools API.
    """

    def __init__(
        self,
        scan: Callable[[], None],
        interval: float | None = None,
    ) -> None:
        if not callable(scan):
            raise TypeError("scan must be callable")
        self.interval = _configured_interval() if interval is None else interval
        if not isinstance(self.interval, (int, float)) or not math.isfinite(self.interval) or self.interval <= 0:
            raise ValueError("Watcher interval must be a positive number of seconds")
        self._scan = scan
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        """Whether the background thread is currently alive."""
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start scanning immediately, then repeat at the configured interval."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="brain-watcher",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> bool:
        """Request shutdown and wait up to ``timeout`` seconds.

        Returns true if the worker has stopped. A currently executing scan is
        not interrupted; it must return before the thread can stop.
        """
        self._stop_event.set()
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout)
        return not thread.is_alive()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._scan()
            except Exception:
                logger.exception("Watcher scan failed; the next scheduled scan will continue")
            if self._stop_event.wait(self.interval):
                break
