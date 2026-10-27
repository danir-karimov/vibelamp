"""Тесты DFU-фолбэка, выбора стратегии и сквозного обновления через Lamp."""

from __future__ import annotations

import subprocess

import pytest

import vibelamp
from vibelamp import ota
from vibelamp.errors import FirmwareUpdateError, FlashFailedError
from vibelamp.simulator import LampSimulator
from vibelamp.transport import LoopbackTransport

IMAGE = bytes(range(256)) * 2


def open_sim(sim: LampSimulator) -> LoopbackTransport:
    transport = sim.attach()
    transport.open()
    transport.reset_input()
    return transport


# --------------------------------------------------------------------------- #
# DFU
# --------------------------------------------------------------------------- #
def test_update_dfu_requires_binary(monkeypatch) -> None:
    monkeypatch.setattr(ota.shutil, "which", lambda name: None)
    with pytest.raises(FirmwareUpdateError) as exc:
        ota.update_dfu(IMAGE)
    assert "dfu-util" in str(exc.value)


def test_update_dfu_success(monkeypatch) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="Done", stderr="")

    monkeypatch.setattr(ota.shutil, "which", lambda name: "/usr/bin/dfu-util")
    monkeypatch.setattr(ota.subprocess, "run", fake_run)

    result = ota.update_dfu(IMAGE, version_before="1.0.25")
    assert result.success is True
    assert result.strategy == "dfu"
    assert result.bytes_sent == len(IMAGE)
    assert calls[0][0] == "/usr/bin/dfu-util"
    assert "-a" in calls[0] and "0x08000000:leave" in calls[0]
    assert calls[0][-2] == "-D"


def test_update_dfu_failure(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="",
                                           stderr="unable to open DFU device")

    monkeypatch.setattr(ota.shutil, "which", lambda name: "/usr/bin/dfu-util")
    monkeypatch.setattr(ota.subprocess, "run", fake_run)
    with pytest.raises(FlashFailedError):
        ota.update_dfu(IMAGE)


def test_update_dfu_accepts_file(tmp_path, monkeypatch) -> None:
    image_file = tmp_path / "vibelamp.bin"
    image_file.write_bytes(IMAGE)
    captured = {}

    def fake_run(command, **kwargs):
        captured["file"] = command[-1]
        with open(command[-1], "rb") as handle:
            captured["size"] = len(handle.read())
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(ota.shutil, "which", lambda name: "dfu-util")
    monkeypatch.setattr(ota.subprocess, "run", fake_run)
    result = ota.update_dfu(str(image_file))
    assert result.success is True
    assert captured["size"] == len(IMAGE)


# --------------------------------------------------------------------------- #
# Стратегия
# --------------------------------------------------------------------------- #
def test_update_firmware_rejects_unknown_strategy() -> None:
    transport = open_sim(LampSimulator())
    with pytest.raises(FirmwareUpdateError):
        ota.update_firmware(IMAGE, transport=transport, strategy="magic")


def test_update_firmware_auto_falls_back_to_dfu(monkeypatch) -> None:
    transport = open_sim(LampSimulator())
    called = {}
    progress = []

    def boom(*args, **kwargs):
        raise FlashFailedError("IAP сломался")

    def fake_dfu(image, **kwargs):
        called["dfu"] = True
        return ota.UpdateResult(success=True, strategy="dfu")

    monkeypatch.setattr(ota, "update_iap", boom)
    monkeypatch.setattr(ota, "update_dfu", fake_dfu)
    monkeypatch.setattr(ota, "dfu_available", lambda: True)

    result = ota.update_firmware(IMAGE, transport=transport,
                                 on_progress=progress.append)
    assert called.get("dfu") is True
    assert result.strategy == "dfu"
    assert any(event.phase == "dfu" for event in progress)


def test_update_firmware_auto_without_dfu(monkeypatch) -> None:
    transport = open_sim(LampSimulator())

    def boom(*args, **kwargs):
        raise FlashFailedError("нет связи")

    monkeypatch.setattr(ota, "update_iap", boom)
    monkeypatch.setattr(ota, "dfu_available", lambda: False)
    with pytest.raises(FirmwareUpdateError) as exc:
        ota.update_firmware(IMAGE, transport=transport)
    assert "BOOT0" in (exc.value.hint or "")


def test_update_firmware_iap_strategy_has_no_fallback(monkeypatch) -> None:
    transport = open_sim(LampSimulator())

    def boom(*args, **kwargs):
        raise FlashFailedError("нет связи")

    monkeypatch.setattr(ota, "update_iap", boom)
    monkeypatch.setattr(ota, "dfu_available", lambda: True)
    with pytest.raises(FlashFailedError):
        ota.update_firmware(IMAGE, transport=transport, strategy="iap")


# --------------------------------------------------------------------------- #
# Сквозной путь через Lamp
# --------------------------------------------------------------------------- #
def test_lamp_update_firmware_end_to_end() -> None:
    sim = LampSimulator(version="1.0.25", new_version="1.0.26", echo=False)
    lamp = vibelamp.connect(sim.attach(), timeout=0.5, retries=0)
    assert str(lamp.firmware_version) == "1.0.25"

    result = lamp.update_firmware(IMAGE, pause=0)
    assert result.success is True
    assert result.version_after == "1.0.26"
    assert str(lamp.firmware_version) == "1.0.26"

    lamp.set_rgb(1, 2, 3)          # управление работает и после прошивки
    assert sim.rgb == (1, 2, 3)
    lamp.close()


def test_recovery_instructions_mention_boot0() -> None:
    text = ota.recovery_instructions()
    assert "BOOT0" in text and "dfu-util" in text
