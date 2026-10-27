"""Тесты иерархии ошибок SDK."""

from __future__ import annotations

import pytest

from vibelamp import errors as E


def test_all_errors_derive_from_base() -> None:
    classes = [obj for name, obj in vars(E).items()
               if name.endswith("Error") and isinstance(obj, type)]
    assert classes, "ошибки не найдены"
    for cls in classes:
        assert issubclass(cls, E.VibeLampError), cls


def test_domain_grouping() -> None:
    assert issubclass(E.DeviceNotFoundError, E.DiscoveryError)
    assert issubclass(E.PortBusyError, E.TransportError)
    assert issubclass(E.DeviceTimeoutError, E.ProtocolError)
    assert issubclass(E.FlashFailedError, E.FirmwareUpdateError)


def test_validation_error_is_also_value_error() -> None:
    with pytest.raises(ValueError):
        raise E.ValidationError("bri вне диапазона 0..255")


def test_hint_is_rendered_and_kept() -> None:
    err = E.PermissionDeniedError("Нет прав на /dev/ttyACM0", "udev-правило 0483:5740")
    assert err.message == "Нет прав на /dev/ttyACM0"
    assert err.hint == "udev-правило 0483:5740"
    assert "udev-правило" in str(err)


def test_device_not_found_default_message() -> None:
    err = E.DeviceNotFoundError()
    assert "0483" in err.message and "5740" in err.message
