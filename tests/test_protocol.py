"""Тесты транзактора CLI-протокола против симулятора прошивки."""

from __future__ import annotations

import pytest

from vibelamp.errors import (DeviceRejectedError, DeviceTimeoutError,
                           UnexpectedResponseError)
from vibelamp.protocol import ProtocolClient, parse_status
from vibelamp.simulator import LampSimulator
from vibelamp.transport import LoopbackTransport
from vibelamp.versioning import FirmwareVersion

STATUS_TEXT = (
    "version: 1.0.25\r\n"
    "effect: wave (params: 3500 / 0)\r\n"
    "color: rgb(0 140 255)\r\n"
    "brightness: 180\r\n"
    "uptime: 41234 ms\r\n"
)


@pytest.fixture
def sim() -> LampSimulator:
    return LampSimulator(version="1.0.25")


@pytest.fixture
def client(sim: LampSimulator) -> ProtocolClient:
    c = ProtocolClient(sim.attach(), timeout=0.5, retries=0, quiet_ms=60)
    c.open()
    yield c
    c.close()


# --------------------------------------------------------------------------- #
# Парсер статуса
# --------------------------------------------------------------------------- #
def test_parse_status_full() -> None:
    snap = parse_status(STATUS_TEXT)
    assert snap.version == FirmwareVersion(1, 0, 25)
    assert snap.effect == "wave"
    assert snap.params == (3500, 0)
    assert snap.rgb == (0, 140, 255)
    assert snap.brightness == 180
    assert snap.uptime_ms == 41234
    assert snap.is_on is True


def test_parse_status_partial_and_empty() -> None:
    snap = parse_status("brightness: 0\r\n")
    assert snap.brightness == 0
    assert snap.effect is None
    assert snap.is_on is False
    assert parse_status("").version is None


def test_parse_status_tolerates_noise() -> None:
    snap = parse_status("Welcome to CLI VibeLamp v1.0.25\r\n" + STATUS_TEXT)
    assert snap.version == FirmwareVersion(1, 0, 25)


def test_status_to_dict() -> None:
    snap = parse_status(STATUS_TEXT).to_dict()
    assert snap["version"] == "1.0.25"
    assert snap["rgb"] == [0, 140, 255]
    assert snap["params"] == [3500, 0]


# --------------------------------------------------------------------------- #
# Транзактор
# --------------------------------------------------------------------------- #
def test_open_disables_echo(sim: LampSimulator) -> None:
    ProtocolClient(sim.attach(), timeout=0.5).open()
    assert sim.echo_enabled is False


def test_version_command(client: ProtocolClient) -> None:
    assert client.version() == FirmwareVersion(1, 0, 25)


def test_status_command(client: ProtocolClient) -> None:
    snap = client.status()
    assert snap.version == FirmwareVersion(1, 0, 25)
    assert snap.brightness == 180


def test_command_ok_reply(client: ProtocolClient, sim: LampSimulator) -> None:
    assert client.command("rgb 10 20 30") == "OK"
    assert sim.rgb == (10, 20, 30)


def test_command_with_prefixed_ok(client: ProtocolClient) -> None:
    assert client.command("on") == "OK: on"


def test_rejected_command(client: ProtocolClient) -> None:
    with pytest.raises(DeviceRejectedError):
        client.command("rgb 300 0 0")


def test_unknown_command(client: ProtocolClient) -> None:
    with pytest.raises(DeviceRejectedError):
        client.command("unicorns")


def test_timeout_when_device_silent() -> None:
    silent = LoopbackTransport(name="silent")
    silent.open()
    c = ProtocolClient(silent, timeout=0.05, retries=0, disable_echo=False)
    with pytest.raises(DeviceTimeoutError):
        c.command("rgb 0 0 0")


def test_retry_then_success() -> None:
    sim = LampSimulator()
    c = ProtocolClient(sim.attach(), timeout=0.4, retries=2)
    c.open()
    assert c.command("bri 120") == "OK"


def test_query_multiline(client: ProtocolClient) -> None:
    lines = client.query("status")
    assert lines[0].startswith("version:")
    assert any(line.startswith("uptime:") for line in lines)


def test_help_text(client: ProtocolClient) -> None:
    assert "VibeLamp CLI" in client.help_text()


def test_query_timeout_raises() -> None:
    silent = LoopbackTransport(name="silent")
    silent.open()
    c = ProtocolClient(silent, timeout=0.05, retries=0, disable_echo=False)
    with pytest.raises(DeviceTimeoutError):
        c.query("status")


def test_line_length_guard(client: ProtocolClient) -> None:
    with pytest.raises(ValueError):
        client.command("rgb " + "1 " * 40)
    with pytest.raises(ValueError):
        client.command("   ")


def test_set_echo_roundtrip(client: ProtocolClient, sim: LampSimulator) -> None:
    client.set_echo(True)
    assert sim.echo_enabled is True
    client.set_echo(False)
    assert sim.echo_enabled is False


# --------------------------------------------------------------------------- #
# Устойчивость к шуму на линии (баннер, посимвольное эхо) — проверено на fw 1.0.32
# --------------------------------------------------------------------------- #
def test_banner_before_reply_is_ignored() -> None:
    def handler(tx: bytes) -> bytes:
        return b"Welcome to CLI VibeLamp v1.0.32, send /h for print help\r\nOK\r\n"

    tr = LoopbackTransport(name="noisy", handler=handler)
    tr.open()
    client = ProtocolClient(tr, timeout=0.5, retries=0, disable_echo=False)
    assert client.command("rgb 1 2 3") == "OK"


def test_char_echo_noise_is_ignored() -> None:
    def handler(tx: bytes) -> bytes:
        noise = b"".join(bytes([b]) + b"\r\n" for b in b"rgb 1 2 3")
        return noise + b"OK\r\n"

    tr = LoopbackTransport(name="char-echo", handler=handler)
    tr.open()
    client = ProtocolClient(tr, timeout=0.5, retries=0, disable_echo=False)
    assert client.command("rgb 1 2 3") == "OK"


def test_query_skips_noise_before_block() -> None:
    def handler(tx: bytes) -> bytes:
        return (b"garbage bytes\r\n"
                b"version: 1.0.32\r\n")

    tr = LoopbackTransport(name="noisy-query", handler=handler)
    tr.open()
    client = ProtocolClient(tr, timeout=0.5, disable_echo=False, quiet_ms=40)
    assert client.version() == FirmwareVersion(1, 0, 32)
