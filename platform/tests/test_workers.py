import logging
import threading

import pytest

from platform.workers import Watcher


def test_watcher_uses_environment_interval_and_continues_after_scan_error(monkeypatch, caplog):
    monkeypatch.setenv("WATCHER_INTERVAL", "0.01")
    calls = 0
    scanned_twice = threading.Event()

    def scan():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary scan failure")
        scanned_twice.set()

    watcher = Watcher(scan)
    assert watcher.interval == 0.01

    with caplog.at_level(logging.ERROR, logger="platform.workers"):
        watcher.start()
        try:
            assert scanned_twice.wait(timeout=1)
            assert "Watcher scan failed" in caplog.text
        finally:
            assert watcher.stop(timeout=1)

    completed_calls = calls
    threading.Event().wait(0.03)
    assert calls == completed_calls
    assert not watcher.is_running


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "invalid"])
def test_watcher_rejects_invalid_configured_interval(monkeypatch, value):
    monkeypatch.setenv("WATCHER_INTERVAL", value)
    with pytest.raises(ValueError, match="WATCHER_INTERVAL"):
        Watcher(lambda: None)


def test_watcher_rejects_invalid_explicit_interval():
    with pytest.raises(ValueError, match="Watcher interval"):
        Watcher(lambda: None, interval=0)
