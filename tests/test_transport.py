"""Тесты транспортного слоя (без реального железа)."""

from __future__ import annotations

import pytest

from vibelamp.errors import ConnectionLostError, TransportError
from vibelamp.transport import MAX_LINE_LEN, LoopbackTransport, SerialTransport, Transport


# --------------------------------------------------------------------------- #
# LoopbackTransport
# --------------------------------------------------------------------------- #
def test_loopback_open_close(loopback: LoopbackTransport) -> None:
    assert loopback.is_open is True
    loopback.close()
    assert loopback.is_open is False
    loopback.close()  # повторное закрытие не ошибка


def test_loopback_is_a_transport(loopback: LoopbackTransport) -> None:
    assert isinstance(loopback, Transport)


def test_write_line_splits_tx_lines(loopback: LoopbackTransport) -> None:
    loopback.write_line("rgb 255 0 0")
    loopback.write_line("bri 180")
    assert loopback.tx_lines == ["rgb 255 0 0", "bri 180"]
    assert loopback.tx_log.endswith(b"bri 180\r\n")


def test_write_line_strips_user_terminators(loopback: LoopbackTransport) -> None:
    loopback.write_line("status\r\n")
    assert loopback.tx_lines == ["status"]
    assert bytes(loopback.tx_log) == b"status\r\n"


def test_handler_reply_is_readable(loopback: LoopbackTransport) -> None:
    loopback.write_line("rgb 1 2 3")
    assert loopback.read_line(timeout=0.5) == "OK"


def test_read_until_stops_on_needle(loopback: LoopbackTransport) -> None:
    loopback.feed(b"version: 1.0.25\r\n")
    assert loopback.read_until(b"\n", timeout=0.5) == b"version: 1.0.25\r\n"
    # буфер пуст -> следующий read ждёт до таймаута и возвращает пустоту
    assert loopback.read_until(b"\n", timeout=0.02) == b""


def test_read_until_timeout_returns_partial(loopback: LoopbackTransport) -> None:
    loopback.feed(b"OK: par")
    assert loopback.read_until(b"\n", timeout=0.02) == b"OK: par"


def test_read_available_and_reset(loopback: LoopbackTransport) -> None:
    loopback.feed(b"abc")
    assert loopback.read_available() == b"abc"
    loopback.feed(b"xyz")
    loopback.reset_input()
    assert loopback.read_available() == b""


def test_write_on_closed_channel_raises() -> None:
    tr = LoopbackTransport()
    with pytest.raises(ConnectionLostError):
        tr.write(b"on\r\n")


def test_context_manager_opens_and_closes() -> None:
    with LoopbackTransport(handler=lambda tx: b"OK\r\n") as tr:
        assert tr.is_open
        tr.write_line("off")
        assert tr.read_line(timeout=0.5) == "OK"
    assert tr.is_open is False


def test_max_line_len_constant() -> None:
    assert MAX_LINE_LEN == 64


# --------------------------------------------------------------------------- #
# SerialTransport (только поведение без открытия порта)
# --------------------------------------------------------------------------- #
def test_serial_write_before_open_raises() -> None:
    tr = SerialTransport("COM-X-DOES-NOT-EXIST")
    assert tr.is_open is False
    with pytest.raises(ConnectionLostError):
        tr.write(b"on\r\n")


def test_serial_close_is_idempotent() -> None:
    tr = SerialTransport("COM-X-DOES-NOT-EXIST")
    tr.close()
    tr.close()
    assert tr.read_available() == b""
    tr.reset_input()


def test_serial_error_translation() -> None:
    import serial

    tr = SerialTransport("/dev/ttyACM9")
    busy = tr._translate(serial.SerialException(
        "could not open port 'COM3': PermissionError(13, 'Access is denied')"))
    assert isinstance(busy, TransportError)
    missing = tr._translate(serial.SerialException(
        "could not open port 'COM9': FileNotFoundError(2, 'The system cannot "
        "find the file specified.')"))
    assert isinstance(missing, TransportError)
    generic = tr._translate(RuntimeError("boom"))
    assert isinstance(generic, TransportError)
