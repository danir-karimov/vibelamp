"""Сквозные тесты высокоуровневого клиента против симулятора прошивки."""

from __future__ import annotations

import pytest

import vibelamp
from vibelamp.effects import AgentState, Effect
from vibelamp.errors import DeviceRejectedError, ValidationError
from vibelamp.simulator import LampSimulator


@pytest.fixture
def sim() -> LampSimulator:
    return LampSimulator(version="1.0.25", brightness=200)


@pytest.fixture
def lamp(sim: LampSimulator):
    client = vibelamp.connect(sim.attach(), timeout=0.5, retries=0)
    yield client
    client.close()


def test_connect_reads_version(lamp, sim) -> None:
    assert lamp.is_open is True
    assert str(lamp.firmware_version) == "1.0.25"
    assert lamp.path == "sim"


def test_connect_applies_brightness(lamp, sim) -> None:
    client = vibelamp.connect(sim.attach(), brightness=120, timeout=0.5, retries=0)
    assert sim.brightness == 120
    client.close()


def test_brightness_is_limited_by_max(lamp, sim) -> None:
    client = vibelamp.connect(sim.attach(), brightness=250, max_brightness=180,
                            timeout=0.5, retries=0)
    assert sim.brightness == 180
    client.set_brightness(255)
    assert sim.brightness == 180
    client.set_max_brightness(90)
    assert sim.brightness == 90
    client.close()


def test_set_rgb(lamp, sim) -> None:
    lamp.set_rgb(255, 128, 0)
    assert sim.rgb == (255, 128, 0)
    assert sim.effect_name == "solid"


def test_off_and_on(lamp, sim) -> None:
    lamp.set_rgb(10, 20, 30)
    assert lamp.off() == "OK: off"
    assert sim.rgb == (0, 0, 0)
    assert lamp.on() == "OK: on"


def test_effect_object(lamp, sim) -> None:
    lamp.effect(Effect.rainbow(speed_ms=40))
    assert sim.effect_name == "rainbow"
    assert sim.params == (40, 0)


def test_effect_string_escape_hatch(lamp, sim) -> None:
    lamp.effect("wave 3500 0 140 255")
    assert sim.effect_name == "wave"
    assert sim.params == (3500, 0)


def test_effect_agent_state(lamp, sim) -> None:
    lamp.effect(AgentState.COMPLETED)
    assert sim.effect_name == "mode_completed"


def test_set_mode_string_and_enum(lamp, sim) -> None:
    lamp.set_mode("waiting")
    assert sim.effect_name == "mode_waiting"
    lamp.set_mode(AgentState.REASONING)
    assert sim.effect_name == "mode_reasoning"


def test_set_mode_validates_name(lamp) -> None:
    with pytest.raises(ValidationError):
        lamp.set_mode("sleeping")


def test_invalid_level_rejected_before_send(lamp, sim) -> None:
    sent_before = list(sim.received_image)
    with pytest.raises(ValidationError):
        lamp.set_brightness(300)
    with pytest.raises(ValidationError):
        lamp.set_rgb(0, -5, 0)
    assert sim.rgb == (0, 0, 0)
    assert sent_before == []


def test_device_rejects_bad_arguments(lamp) -> None:
    with pytest.raises(DeviceRejectedError):
        lamp.effect("blink 5")


def test_status_snapshot(lamp) -> None:
    lamp.set_rgb(1, 2, 3)
    snap = lamp.status()
    assert snap.rgb == (1, 2, 3)
    assert snap.effect == "solid"
    assert snap.uptime_ms and snap.uptime_ms > 0
    assert snap.to_dict()["rgb"] == [1, 2, 3]


def test_version_query(lamp) -> None:
    assert str(lamp.version()) == "1.0.25"


def test_help_text(lamp) -> None:
    assert "VibeLamp CLI" in lamp.help_text()


def test_batch_executes_in_order(lamp, sim) -> None:
    replies = lamp.batch([Effect("rgb", (1, 2, 3)), "bri 100", Effect("off")])
    assert replies == ["OK", "OK", "OK: off"]
    assert sim.rgb == (0, 0, 0)
    assert sim.brightness == 100


def test_batch_stops_on_first_error(lamp) -> None:
    with pytest.raises(DeviceRejectedError):
        lamp.batch(["bri 10", "rgb 999 0 0", "bri 20"])


def test_raw_query_fallback(lamp) -> None:
    assert "version:" in lamp.raw("version")


def test_raw_rejects_empty(lamp) -> None:
    with pytest.raises(ValueError):
        lamp.raw("   ")


def test_lease_blocks_other_threads(lamp) -> None:
    import threading

    started = threading.Event()
    finished = threading.Event()

    def worker():
        started.set()
        lamp.set_brightness(111)
        finished.set()

    with lamp.lease("flashing"):
        thread = threading.Thread(target=worker)
        thread.start()
        started.wait(1.0)
        # lease удерживается, поэтому команда не могла пройти
        assert finished.wait(0.2) is False
    thread.join(timeout=2.0)
    assert finished.is_set()


def test_context_manager_closes(sim) -> None:
    with vibelamp.connect(sim.attach(), timeout=0.5, retries=0) as lamp:
        assert lamp.is_open
    assert lamp.is_open is False


def test_lamp_accepts_device_info(sim) -> None:
    from vibelamp.discovery import DeviceInfo

    info = DeviceInfo(path="sim", firmware_version=None)
    lamp = vibelamp.connect(info, opener=sim.opener(), timeout=0.5, retries=0)
    assert str(lamp.version()) == "1.0.25"
    lamp.close()


def test_lamp_rejects_bad_device_type() -> None:
    with pytest.raises(ValidationError):
        vibelamp.Lamp(12345)
