"""Тесты обнаружения: перечисление портов и probe-handshake."""

from __future__ import annotations

import pytest

import serial.tools.list_ports as list_ports_module

from vibelamp.discovery import (DeviceInfo, find_first, list_candidate_ports,
                              probe_version, scan)
from vibelamp.errors import DeviceNotFoundError
from vibelamp.transport import LoopbackTransport
from vibelamp.versioning import FirmwareVersion


class FakePort:
    """Миниатура записи ``serial.tools.list_ports.ListPortInfo``."""

    def __init__(self, device, vid, pid, serial_number=None,
                 product=None, manufacturer=None):
        self.device, self.vid, self.pid = device, vid, pid
        self.serial_number, self.product, self.manufacturer = (
            serial_number, product, manufacturer)


@pytest.fixture
def fake_comports(monkeypatch):
    """Подменить системное перечисление портов."""
    def _set(ports):
        monkeypatch.setattr(list_ports_module, "comports",
                            lambda *a, **k: list(ports))
    return _set


def make_opener(responses: dict):
    """Фабрика транспорта: каждому пути — свой «ответ прошивки»."""
    def opener(path: str) -> LoopbackTransport:
        return LoopbackTransport(name=path, initial_rx=responses.get(path, b"OK\r\n"))
    return opener


LAMP = FakePort("COM7", 0x0483, 0x5740, serial_number="ABC123",
                product="STMicroelectronics Virtual COM Port")
OTHER_ST = FakePort("COM8", 0x0483, 0x5740, serial_number="XYZ999")
FOREIGN = FakePort("COM9", 0x239A, 0x8029, serial_number="OTHER")


# --------------------------------------------------------------------------- #
# list_candidate_ports
# --------------------------------------------------------------------------- #
def test_filter_by_vid_pid(fake_comports) -> None:
    fake_comports([LAMP, OTHER_ST, FOREIGN])
    assert [c.path for c in list_candidate_ports()] == ["COM7", "COM8"]


def test_explicit_ports_skip_vid_filter(fake_comports) -> None:
    fake_comports([])
    assert [c.path for c in list_candidate_ports(ports=["COM42"])] == ["COM42"]


def test_no_ports_returns_empty(fake_comports) -> None:
    fake_comports([])
    assert scan() == []


# --------------------------------------------------------------------------- #
# probe-handshake
# --------------------------------------------------------------------------- #
def test_probe_version_parses_reply() -> None:
    opener = make_opener({"COM7": b"version: 1.0.25\r\n"})
    assert probe_version("COM7", timeout_ms=100, opener=opener) == FirmwareVersion(1, 0, 25)


def test_probe_version_rejects_foreign_device() -> None:
    opener = make_opener({"COM9": b"OK\r\n"})
    assert probe_version("COM9", timeout_ms=60, opener=opener) is None


def test_probe_version_timeout_is_none() -> None:
    assert probe_version("COM0", timeout_ms=50, opener=make_opener({})) is None


def test_probe_sends_version_command() -> None:
    tr = LoopbackTransport(name="COM7")
    probe_version("COM7", timeout_ms=50, opener=lambda p: tr)
    assert tr.tx_lines[0] == "version"


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #
def test_scan_keeps_only_confirmed_devices(fake_comports) -> None:
    fake_comports([LAMP, OTHER_ST, FOREIGN])
    opener = make_opener({"COM7": b"version: 1.0.25\r\n"})
    found = scan(timeout_ms=80, opener=opener)
    assert [d.path for d in found] == ["COM7"]
    assert found[0].probed is True
    assert found[0].firmware_version == FirmwareVersion(1, 0, 25)
    assert found[0].serial_number == "ABC123"
    assert found[0].version_string == "1.0.25"


def test_scan_without_probe_returns_all_candidates(fake_comports) -> None:
    fake_comports([LAMP, OTHER_ST])
    found = scan(probe=False)
    assert [d.path for d in found] == ["COM7", "COM8"]
    assert all(d.probed is False for d in found)


def test_device_info_to_dict_and_key(fake_comports) -> None:
    fake_comports([LAMP])
    dev = scan(probe=False)[0]
    assert dev.to_dict() == {
        "path": "COM7", "vid": 0x0483, "pid": 0x5740,
        "serial_number": "ABC123",
        "product": "STMicroelectronics Virtual COM Port",
        "manufacturer": None, "firmware_version": None, "probed": False,
    }
    assert dev.key == "COM7|ABC123"


def test_find_first_raises_when_empty(fake_comports) -> None:
    fake_comports([])
    with pytest.raises(DeviceNotFoundError):
        find_first()


def test_find_first_returns_device(fake_comports) -> None:
    fake_comports([LAMP])
    opener = make_opener({"COM7": b"version: 1.0.25\r\n"})
    assert find_first(timeout_ms=80, opener=opener).path == "COM7"


def test_probe_retries_until_device_answers() -> None:
    """Открытие порта может перезапускать МК: probe повторяет запрос,
    пока устройство не выйдет на связь (проверено на живом fw 1.0.32)."""
    state = {"writes": 0}

    def handler(tx: bytes):
        state["writes"] += 1
        return b"version: 1.0.32\r\n" if state["writes"] > 1 else b""

    transport = LoopbackTransport(name="slow-port", handler=handler)
    version = probe_version("COM9", timeout_ms=700, opener=lambda p: transport)
    assert version == FirmwareVersion(1, 0, 32)
    assert state["writes"] == 2


def test_probe_accepts_boot_banner_as_proof() -> None:
    """Баннер загрузки тоже подтверждает устройство (версия есть в нём)."""
    banner = b"Welcome to CLI VibeLamp v1.0.32, send /h for print help\r\n"
    transport = LoopbackTransport(name="rebooting", initial_rx=banner)
    assert probe_version("COM9", timeout_ms=300,
                         opener=lambda p: transport) == FirmwareVersion(1, 0, 32)


# --------------------------------------------------------------------------- #
# diagnose
# --------------------------------------------------------------------------- #
def test_diagnose_classifies_candidates(fake_comports) -> None:
    from vibelamp.discovery import diagnose

    fake_comports([LAMP, OTHER_ST])

    def opener(path: str):
        if path == "COM8":
            raise RuntimeError("could not open port 'COM8': Access is denied")
        return LoopbackTransport(name=path, initial_rx=b"version: 1.0.25\r\n")

    report = diagnose(opener=opener)
    by_path = {item["path"]: item["status"] for item in report}
    assert by_path == {"COM7": "ok", "COM8": "busy"}


def test_diagnose_detects_silent_port(fake_comports) -> None:
    from vibelamp.discovery import diagnose

    fake_comports([LAMP])
    report = diagnose(timeout_ms=100,
                      opener=lambda p: LoopbackTransport(name=p))
    assert report[0]["status"] == "no_reply"
