"""Тесты платформенного слоя (ОС подменяется monkeypatch'ем)."""

from __future__ import annotations

import pytest

from vibelamp import platform_utils as pf


@pytest.fixture
def as_windows(monkeypatch) -> None:
    monkeypatch.setattr(pf, "current_os", lambda: "windows")


@pytest.fixture
def as_darwin(monkeypatch) -> None:
    monkeypatch.setattr(pf, "current_os", lambda: "darwin")


@pytest.fixture
def as_linux(monkeypatch) -> None:
    monkeypatch.setattr(pf, "current_os", lambda: "linux")


def test_current_os_returns_known_name() -> None:
    assert pf.current_os() in ("windows", "linux", "darwin")


def test_normalize_keeps_low_com_ports(as_windows) -> None:
    assert pf.normalize_port("COM3") == "COM3"


def test_normalize_adds_win32_prefix_for_high_com(as_windows) -> None:
    assert pf.normalize_port("COM12") == "\\\\.\\COM12"
    assert pf.normalize_port("\\\\.\\COM12") == "\\\\.\\COM12"


def test_normalize_is_noop_off_windows(as_linux) -> None:
    assert pf.normalize_port("COM12") == "COM12"
    assert pf.normalize_port("/dev/ttyACM0") == "/dev/ttyACM0"


def test_prefer_cu_over_tty_on_macos(as_darwin) -> None:
    paths = ["/dev/tty.usbmodem123", "/dev/cu.usbmodem123", "/dev/cu.usbmodem456"]
    assert pf.prefer_serial_paths(paths) == ["/dev/cu.usbmodem123", "/dev/cu.usbmodem456"]


def test_prefer_keeps_all_paths_off_macos(as_linux) -> None:
    paths = ["/dev/ttyACM0", "/dev/ttyACM1"]
    assert pf.prefer_serial_paths(paths) == paths


def test_udev_rule_covers_vcp_and_dfu() -> None:
    rule = pf.udev_rule()
    assert 'idVendor}=="0483"' in rule
    assert 'idProduct}=="5740"' in rule
    assert 'idProduct}=="df11"' in rule
    assert "MODE=\"0666\"" in rule


def test_udev_instructions_mention_reload() -> None:
    assert "udevadm" in pf.udev_install_instructions()


def test_permission_hint_is_os_specific(as_linux, monkeypatch) -> None:
    monkeypatch.setattr(pf, "current_os", lambda: "linux")
    assert "udev" in pf.permission_hint("/dev/ttyACM0")


def test_permission_hint_windows(as_windows, monkeypatch) -> None:
    monkeypatch.setattr(pf, "current_os", lambda: "windows")
    assert "порт занят" in pf.permission_hint("COM5").lower()


def test_is_dfu_id() -> None:
    assert pf.is_dfu_id(0x0483, 0xDF11) is True
    assert pf.is_dfu_id(0x0483, 0x5740) is False
    assert pf.is_dfu_id(None, None) is False


def test_is_busy_error_detects_localized_windows_message() -> None:
    """pyserial пробрасывает системное сообщение на языке ОС."""
    assert pf.is_busy_error(PermissionError(13, "Отказано в доступе")) is True
    assert pf.is_busy_error(RuntimeError(
        "could not open port 'COM15': PermissionError(13, "
        "'Отказано в доступе.', None, 5)")) is True
    assert pf.is_busy_error(RuntimeError(
        "could not open port 'COM3': PermissionError(13, "
        "'Access is denied.', None, 5)")) is True
    assert pf.is_busy_error(RuntimeError("port disappeared")) is False
