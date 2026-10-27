"""Тесты обновления прошивки: заголовок IAP, восстановление, сам протокол."""

from __future__ import annotations

import zlib

import pytest

from vibelamp import ota
from vibelamp.errors import (DeviceStuckError, FirmwareUpdateError,
                           FlashFailedError)
from vibelamp.simulator import LampSimulator
from vibelamp.transport import LoopbackTransport

IMAGE = bytes(range(256)) * 2          # 512-байтный «образ»


def open_sim(sim: LampSimulator) -> LoopbackTransport:
    transport = sim.attach()
    transport.open()
    transport.reset_input()
    return transport


# --------------------------------------------------------------------------- #
# Заголовок и CRC
# --------------------------------------------------------------------------- #
def test_crc32_matches_zlib_vector() -> None:
    assert ota.crc32(b"123456789") == 0xCBF43926


def test_build_header_layout() -> None:
    header = ota.build_header(IMAGE)
    assert len(header) == ota.HEADER_LEN
    assert header[0] == 0xA5 and header[1] == 0x5A
    assert header[2] == ota.HEADER_PROTO and header[3] == ota.HEADER_TYPE_RAW
    assert int.from_bytes(header[4:8], "big") == len(IMAGE)
    assert int.from_bytes(header[8:12], "big") == (zlib.crc32(IMAGE) & 0xFFFFFFFF)


def test_build_header_rejects_bad_image() -> None:
    with pytest.raises(FirmwareUpdateError):
        ota.build_header(b"")
    with pytest.raises(FirmwareUpdateError):
        ota.build_header(b"\x00" * (ota.MAX_IMAGE_SIZE + 1))


# --------------------------------------------------------------------------- #
# cli_alive / ensure_idle
# --------------------------------------------------------------------------- #
def test_cli_alive_on_healthy_device() -> None:
    assert ota.cli_alive(open_sim(LampSimulator())) is True


def test_cli_alive_false_on_silent_channel() -> None:
    silent = LoopbackTransport(name="silent")
    silent.open()
    assert ota.cli_alive(silent, timeout=0.05) is False


def test_ensure_idle_noop_when_cli_alive() -> None:
    assert ota.ensure_idle(open_sim(LampSimulator())) is False


def test_ensure_idle_recovers_stuck_device() -> None:
    sim = LampSimulator()
    sim.stuck_in_update = True
    transport = open_sim(sim)
    assert ota.ensure_idle(transport, timeout=2.0) is True
    assert sim.stuck_in_update is False
    assert ota.cli_alive(transport) is True


def test_ensure_idle_raises_when_dead() -> None:
    dead = LoopbackTransport(name="dead")
    dead.open()
    with pytest.raises(DeviceStuckError):
        ota.ensure_idle(dead, timeout=0.2)


# --------------------------------------------------------------------------- #
# IAP
# --------------------------------------------------------------------------- #
def test_update_iap_success() -> None:
    sim = LampSimulator(version="1.0.25", new_version="1.0.26", echo=False)
    transport = open_sim(sim)
    events = []

    result = ota.update_iap(transport, IMAGE, pause=0, version_before="1.0.25",
                            on_progress=events.append)
    assert result.success is True
    assert result.strategy == "iap"
    assert result.bytes_sent == len(IMAGE)
    assert result.crc32 == ota.crc32(IMAGE)
    assert result.version_before == "1.0.25"
    assert result.version_after == "1.0.26"
    assert sim.flash_success is True
    assert sim.version_str == "1.0.26"

    phases = [e.phase for e in events]
    assert phases[0] == "prepare" and phases[-1] == "done"
    assert "transfer" in phases and "verify" in phases
    assert max(e.percent for e in events) == 100.0
    assert result.to_dict()["crc32"].startswith("0x")


def test_update_iap_reports_not_entering_update_mode() -> None:
    def handler(tx: bytes) -> bytes:
        text = tx.decode("latin-1", "replace")
        if "version" in text:
            return b"version: 1.0.20\r\n"
        if "update" in text:
            return b"Error: unknown command. Send /h for help\r\n"
        return b""

    transport = LoopbackTransport(name="old-fw", handler=handler)
    transport.open()
    with pytest.raises(FlashFailedError):
        ota.update_iap(transport, IMAGE, pause=0, confirm=False)


def test_update_iap_reports_crc_reject() -> None:
    state = {"payload": False}

    def handler(tx: bytes) -> bytes:
        text = tx.decode("latin-1", "replace")
        if "version" in text:
            return b"version: 1.0.20\r\n"
        if text.startswith("update"):
            state["payload"] = True
            return b"OK: update ready. Send 12-byte header then firmware.\r\n"
        if state["payload"]:
            return b"Error: CRC mismatch\r\n"
        return b""

    transport = LoopbackTransport(name="rejecting", handler=handler)
    transport.open()
    with pytest.raises(FlashFailedError):
        ota.update_iap(transport, IMAGE, pause=0, confirm=False)
    assert state["payload"] is True


def test_update_iap_recovers_stuck_device_first() -> None:
    sim = LampSimulator(version="1.0.25", new_version="1.0.26", echo=False)
    sim.stuck_in_update = True
    transport = open_sim(sim)
    result = ota.update_iap(transport, IMAGE, pause=0)
    assert result.success is True
    assert sim.flash_success is True
