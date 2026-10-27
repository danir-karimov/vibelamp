"""Тесты симулятора прошивки (он же — основа CI без железа)."""

from __future__ import annotations

import zlib

import pytest

from vibelamp.simulator import LampSimulator

IMAGE = bytes(range(256)) * 4          # 1024-байтный «образ»


def make_header(body: bytes, magic=(0xA5, 0x5A), size=None) -> bytes:
    length = len(body) if size is None else size
    crc = zlib.crc32(body) & 0xFFFFFFFF
    return bytes([magic[0], magic[1], 1, 0]) + length.to_bytes(4, "big") + crc.to_bytes(4, "big")


@pytest.fixture
def sim() -> LampSimulator:
    # Эхо выключено: ответы читаются однострочно. Отдельные тесты эха
    # создают собственный симулятор с echo=True.
    return LampSimulator(version="1.0.25", new_version="1.0.26", echo=False)


@pytest.fixture
def tr(sim: LampSimulator):
    transport = sim.attach()
    transport.open()
    transport.reset_input()      # отбросить баннер загрузки
    yield transport
    transport.close()


def test_banner_and_version(sim: LampSimulator) -> None:
    transport = sim.attach()
    transport.open()
    banner = transport.read_until(b"\n", timeout=0.5)
    assert b"Welcome to CLI VibeLamp v1.0.25" in banner
    transport.write_line("version")
    assert b"version: 1.0.25" in transport.read_until(b"\n", timeout=0.5)
    transport.close()


def test_echo_on_by_default() -> None:
    loud = LampSimulator(version="1.0.25")
    transport = loud.attach()
    transport.open()
    transport.read_until(b"\n", timeout=0.5)     # баннер
    transport.write_line("on")
    assert transport.read_until(b"\n", timeout=0.5).strip() == b"on"   # эхо
    assert transport.read_until(b"\n", timeout=0.5).strip() == b"OK: on"
    transport.close()


def test_echo_off_silences_input() -> None:
    loud = LampSimulator(version="1.0.25")
    transport = loud.attach()
    transport.open()
    transport.read_until(b"\n", timeout=0.5)     # баннер
    transport.write_line("echo off")
    transport.read_until(b"\n", timeout=0.5)     # эхо
    transport.read_until(b"\n", timeout=0.5)     # OK: echo off
    assert loud.echo_enabled is False
    transport.write_line("on")
    assert transport.read_until(b"\n", timeout=0.5).strip() == b"OK: on"
    transport.close()


def test_invalid_arguments(sim, tr) -> None:
    tr.write_line("rgb 300 0 0")
    assert b"Error: invalid arguments" in tr.read_until(b"\n", timeout=0.5)


def test_unknown_command(sim, tr) -> None:
    tr.write_line("unicorns")
    assert b"Error: unknown command" in tr.read_until(b"\n", timeout=0.5)


def test_status_format(sim, tr) -> None:
    tr.write_line("rgb 1 2 3")
    tr.read_until(b"\n", timeout=0.5)
    tr.write_line("status")
    text = b""
    for _ in range(5):
        text += tr.read_until(b"\n", timeout=0.5)
    decoded = text.decode()
    assert "effect: solid" in decoded
    assert "color: rgb(1 2 3)" in decoded
    assert "uptime:" in decoded


def test_update_success_flow(sim, tr) -> None:
    tr.write_line("update")
    assert b"OK: update ready" in tr.read_until(b"\n", timeout=0.5)
    assert sim.update_state == "awaiting_header"

    tr.write(make_header(IMAGE))
    assert sim.update_state == "awaiting_body"
    tr.write(IMAGE)
    reply = tr.read_until(b"\n", timeout=0.5)
    assert b"OK: verified 1024 bytes" in reply
    assert sim.flash_success is True
    assert sim.version_str == "1.0.26"
    assert sim.update_state == "cli"


def test_update_accepts_split_writes(sim, tr) -> None:
    tr.write_line("update")
    tr.read_until(b"\n", timeout=0.5)
    payload = make_header(IMAGE) + IMAGE
    for i in range(0, len(payload), 64):        # как пейсинг do_update.py
        tr.write(payload[i:i + 64])
    assert b"OK: verified" in tr.read_until(b"\n", timeout=0.5)


def test_update_bad_crc(sim, tr) -> None:
    tr.write_line("update")
    tr.read_until(b"\n", timeout=0.5)
    tr.write(make_header(IMAGE)[:-4] + (0xDEADBEEF).to_bytes(4, "big"))
    tr.write(IMAGE)
    assert b"Error: CRC mismatch" in tr.read_until(b"\n", timeout=0.5)
    assert sim.flash_success is False
    assert sim.version_str == "1.0.25"


def test_update_bad_header(sim, tr) -> None:
    tr.write_line("update")
    tr.read_until(b"\n", timeout=0.5)
    tr.write(make_header(IMAGE, magic=(0x00, 0x00)))
    assert b"Error: bad header magic" in tr.read_until(b"\n", timeout=0.5)
    assert sim.update_state == "cli"


def test_update_oversized_image(sim, tr) -> None:
    tr.write_line("update")
    tr.read_until(b"\n", timeout=0.5)
    tr.write(make_header(IMAGE, size=10 * 1024 * 1024))
    assert b"Error: image too large" in tr.read_until(b"\n", timeout=0.5)


def test_stuck_in_update_ignores_cli_then_recovers(sim, tr) -> None:
    sim.stuck_in_update = True
    tr.write_line("version")
    assert tr.read_until(b"\n", timeout=0.05) == b""     # CLI молчит

    tr.write(b"\xff" * 64)                                # «abort» как do_update.py
    assert b"Error: bad header magic" in tr.read_until(b"\n", timeout=0.5)
    assert sim.stuck_in_update is False

    tr.write_line("version")
    assert b"version: 1.0.25" in tr.read_until(b"\n", timeout=0.5)


def test_dfu_mode_silences_device(sim, tr) -> None:
    tr.write_line("dfu")
    assert b"OK: entering DFU bootloader" in tr.read_until(b"\n", timeout=0.5)
    assert sim.dfu_mode is True
    tr.write_line("version")
    assert tr.read_until(b"\n", timeout=0.05) == b""


def test_opener_returns_same_transport(sim) -> None:
    opener = sim.opener()
    assert opener("/dev/ttyACM0") is opener("COM7")
