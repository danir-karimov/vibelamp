"""Тесты hot-plug подписки (LampWatcher)."""

from __future__ import annotations

import time

from vibelamp.discovery import DeviceInfo, LampWatcher


def _dev(path: str) -> DeviceInfo:
    return DeviceInfo(path=path, serial_number=path)


class FakeScanner:
    """Сканер с programmable-состоянием для проверки diff'а событий."""

    def __init__(self, batches):
        self.batches = list(batches)

    def __call__(self, **_kwargs):
        return self.batches.pop(0) if self.batches else []


def test_poll_once_emits_added_and_removed() -> None:
    added, removed = [], []
    scanner = FakeScanner([[_dev("COM7")], [], [_dev("COM8")]])
    w = LampWatcher(on_added=added.append, on_removed=removed.append, scanner=scanner)
    w.poll_once()
    w.poll_once()
    w.poll_once()
    assert [d.path for d in added] == ["COM7", "COM8"]
    assert [d.path for d in removed] == ["COM7"]
    assert [d.path for d in w.devices] == ["COM8"]


def test_no_duplicate_events_for_same_device() -> None:
    added = []
    scanner = FakeScanner([[_dev("COM7")], [_dev("COM7")], [_dev("COM7")]])
    w = LampWatcher(on_added=added.append, scanner=scanner)
    for _ in range(3):
        w.poll_once()
    assert len(added) == 1


def test_watcher_survives_scanner_exception() -> None:
    def boom(**_kw):
        raise RuntimeError("port enumeration failed")
    w = LampWatcher(scanner=boom)
    assert w.poll_once() == []


def test_watcher_survives_callback_exception() -> None:
    def bad(_dev):
        raise RuntimeError("user callback bug")
    scanner = FakeScanner([[_dev("COM7")]])
    w = LampWatcher(on_added=bad, scanner=scanner)
    w.poll_once()
    assert len(w.devices) == 1


def test_thread_start_stop_and_discovery() -> None:
    scanner = FakeScanner([[_dev("COM7")]] * 200)
    with LampWatcher(interval=0.05, scanner=scanner) as w:
        assert w.running is True
        start = time.monotonic()
        while not w.devices and time.monotonic() - start < 2.0:
            time.sleep(0.01)
        assert [d.path for d in w.devices] == ["COM7"]
    assert w.running is False


def test_start_is_idempotent() -> None:
    scanner = FakeScanner([[_dev("COM7")]] * 200)
    w = LampWatcher(interval=0.05, scanner=scanner)
    w.start()
    first = w._thread
    w.start()
    assert w._thread is first
    w.stop()
