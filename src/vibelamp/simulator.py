"""Программный эмулятор прошивки VibeLamp (для тестов без железа).

Симулятор воспроизводит поведение парсера CLI прошивки VibeLamp и
её модуля обновления flash:

* текстовый CLI с локальным эхом (по умолчанию включено);
* ответы ``OK`` / ``OK: ...`` / ``Error: invalid arguments.`` /
  ``Error: unknown command. Send /h for help``;
* многострочные ``version`` / ``status`` / ``help``;
* IAP-режим: ``update`` -> 12-байтный заголовок -> тело -> проверка CRC32 ->
  ``OK: verified N bytes. Flashing, rebooting...``;
* «застревание» в режиме приёма образа (для проверки восстановления,
  которое выполняет :func:`vibelamp.ota.ensure_idle`).

Используется через :class:`~vibelamp.transport.LoopbackTransport`::

    sim = LampSimulator(version="1.0.25")
    lamp = vibelamp.connect(sim.attach())
"""

from __future__ import annotations

import time
import zlib
from typing import Optional, Tuple

from .effects import EFFECT_SPECS
from .transport import LoopbackTransport
from .versioning import FirmwareVersion, parse_version

__all__ = ["LampSimulator", "SIM_UPDATE_READY", "SIM_VERIFIED_TPL"]

SIM_UPDATE_READY = b"OK: update ready. Send 12-byte header then firmware.\r\n"
SIM_VERIFIED_TPL = "OK: verified %d bytes. Flashing, rebooting...\r\n"
SIM_UNKNOWN = "Error: unknown command. Send /h for help\r\n"
SIM_BAD_ARGS = "Error: invalid arguments. %s\r\n"
SIM_BAD_HEADER = "Error: bad header magic\r\n"
SIM_BAD_CRC = "Error: CRC mismatch\r\n"
SIM_BAD_SIZE = "Error: image too large\r\n"

#: Максимальный размер образа (staging-слот прошивки, байт).
SIM_MAX_IMAGE = 128 * 1024

_HELP_TEXT = (
    "VibeLamp CLI\r\n"
    "  rgb <r> <g> <b>         - solid color\r\n"
    "  bri <val>               - brightness 0..255\r\n"
    "  rainbow <speed_ms>      - rainbow effect\r\n"
    "  status                  - lamp state: effect, color, brightness, uptime\r\n"
    "  version                 - firmware version\r\n"
    "  update                  - enter update mode\r\n"
)


class LampSimulator:
    """Эмулятор состояния и протокола светильника VibeLamp."""

    def __init__(self, version: str = "1.0.25", echo: bool = True,
                 brightness: int = 180, new_version: Optional[str] = None,
                 accept_bad_crc: bool = False) -> None:
        self.version_str = version
        #: Версию, которую устройство «покажет» после успешного обновления.
        self.new_version = new_version
        self.accept_bad_crc = accept_bad_crc
        self.echo_enabled = echo
        self.brightness = brightness
        self.effect_name = "idle"
        self.params: Tuple[int, int] = (0, 0)
        self.rgb: Tuple[int, int, int] = (0, 0, 0)
        self.uptime_ms = 1000
        self.started_at = time.monotonic()

        # --- состояние IAP ---
        #: ``cli`` | ``awaiting_header`` | ``awaiting_body``
        self.update_state = "cli"
        self.received_image = bytearray()
        self.expected_size = 0
        self.expected_crc = 0
        self.flash_attempts = 0
        self.flash_success = False
        #: Принудительно игнорировать CLI (имитация застрявшей прошивки).
        self.stuck_in_update = False
        #: Устройство «ушло» в DFU-загрузчик (CLI недоступен).
        self.dfu_mode = False

        self._line_buf = bytearray()
        self._header_buf = bytearray()

    # ------------------------------------------------------------------ helpers
    @property
    def version(self) -> FirmwareVersion:
        return parse_version(self.version_str) or FirmwareVersion(0, 0, 0)

    def attach(self, name: str = "sim") -> LoopbackTransport:
        """Транспорт, подключённый к этому симулятору (с баннером загрузки)."""
        banner = ("Welcome to CLI VibeLamp v%s, send /h for print help\r\n"
                  % self.version_str).encode()
        return LoopbackTransport(name=name, handler=self.handle, initial_rx=banner)

    def opener(self, name: str = "sim"):
        """Фабрика транспортов для ``scan``/``connect`` (инъекция вместо порта)."""
        transport = self.attach(name)

        def _open(_path: str) -> LoopbackTransport:
            return transport
        return _open

    # ------------------------------------------------------------------ dispatch
    def handle(self, tx: bytes) -> bytes:
        """Обработчик записей для :class:`LoopbackTransport`."""
        if self.dfu_mode:
            return b""
        if self.update_state != "cli" or self.stuck_in_update:
            return self._consume_payload(bytes(tx))
        return self._consume_text(bytes(tx))

    def _consume_text(self, tx: bytes) -> bytes:
        self._line_buf.extend(tx)
        out = bytearray()
        while b"\n" in self._line_buf:
            raw, _, rest = bytes(self._line_buf).partition(b"\n")
            self._line_buf = bytearray(rest)
            line = raw.decode("latin-1", "replace").rstrip("\r")
            if self.echo_enabled:
                out.extend(line.encode("latin-1", "replace") + b"\r\n")
            command = line.strip()
            if not command:
                continue
            out.extend(self._dispatch(command))
        return bytes(out)

    def _dispatch(self, command: str) -> bytes:
        parts = command.split()
        name = parts[0].lower()
        args = parts[1:]

        if name in ("help", "/h"):
            return _HELP_TEXT.encode()
        if name in ("version", "ver"):
            return ("version: %s\r\n" % self.version_str).encode()
        if name == "status":
            return self._status_text().encode()
        if name == "echo":
            if len(args) != 1 or args[0].lower() not in ("on", "off"):
                return (SIM_BAD_ARGS % "echo on | echo off").encode()
            self.echo_enabled = args[0].lower() == "on"
            return ("OK: echo %s\r\n" % args[0].lower()).encode()
        if name == "on":
            return b"OK: on\r\n"
        if name == "off":
            self.rgb = (0, 0, 0)
            self.effect_name = "off"
            return b"OK: off\r\n"
        if name == "stop":
            return b"OK: effect stopped, last frame frozen\r\n"
        if name == "mode":
            return self._mode(args)
        if name == "update":
            self._enter_update()
            return SIM_UPDATE_READY
        if name == "dfu":
            self.dfu_mode = True
            return b"OK: entering DFU bootloader...\r\n"
        return self._effect(name, args)

    def _effect(self, name: str, args) -> bytes:
        spec = EFFECT_SPECS.get(name)
        if spec is None:
            return SIM_UNKNOWN.encode()
        usage = name + " " + " ".join(p[0] for p in spec)
        if len(args) != len(spec):
            return (SIM_BAD_ARGS % usage).encode()
        values = []
        for (arg_name, lo, hi), raw in zip(spec, args):
            if not raw.isdigit():
                return (SIM_BAD_ARGS % usage).encode()
            value = int(raw)
            if not lo <= value <= hi:
                return (SIM_BAD_ARGS % usage).encode()
            values.append(value)
        if name == "rgb":
            self.rgb = (values[0], values[1], values[2])
            self.effect_name = "solid"
        elif name == "bri":
            self.brightness = values[0]
        else:
            self.effect_name = name
            self.params = (values[0], values[1] if len(values) > 1 else 0)
        return b"OK\r\n"

    def _mode(self, args) -> bytes:
        from .effects import MODE_NAMES

        if len(args) != 1 or args[0].lower() not in MODE_NAMES:
            return (SIM_BAD_ARGS % "mode <reasoning|act|waiting|completed|error|idle>").encode()
        self.effect_name = "mode_" + args[0].lower()
        return b"OK\r\n"

    def _status_text(self) -> str:
        self.uptime_ms = 1000 + int((time.monotonic() - self.started_at) * 1000)
        return ("version: %s\r\n"
                "effect: %s (params: %d / %d)\r\n"
                "color: rgb(%d %d %d)\r\n"
                "brightness: %d\r\n"
                "uptime: %d ms\r\n") % (
            self.version_str, self.effect_name, self.params[0], self.params[1],
            self.rgb[0], self.rgb[1], self.rgb[2], self.brightness, self.uptime_ms)

    # ---------------------------------------------------------------------- IAP
    def _enter_update(self) -> None:
        self.update_state = "awaiting_header"
        self.received_image = bytearray()
        self._header_buf = bytearray()
        self.flash_attempts += 1
        self.flash_success = False

    def _consume_payload(self, tx: bytes) -> bytes:
        if self.update_state == "cli":
            # stuck_in_update: устройство «думает», что принимает остаток
            # образа — первый же блок оно прочитает как заголовок.
            self._header_buf.extend(tx)
            if len(self._header_buf) < 12:
                return b""
            extra = bytes(self._header_buf[12:])
            self._header_buf = bytearray()
            self.stuck_in_update = False
            # Прошивка при выходе из режима приёма сбрасывает строковый буфер
            # CLI: «мусор», принятый как payload, не должен превращаться в команду.
            self._line_buf.clear()
            del extra
            return SIM_BAD_HEADER.encode()
        if self.update_state == "awaiting_header":
            self._header_buf.extend(tx)
            if len(self._header_buf) < 12:
                return b""
            header = bytes(self._header_buf[:12])
            extra = bytes(self._header_buf[12:])
            self._header_buf = bytearray()
            if header[0] != 0xA5 or header[1] != 0x5A:
                self.update_state = "cli"
                return SIM_BAD_HEADER.encode()
            self.expected_size = int.from_bytes(header[4:8], "big")
            self.expected_crc = int.from_bytes(header[8:12], "big")
            if not 0 < self.expected_size <= SIM_MAX_IMAGE:
                self.update_state = "cli"
                return SIM_BAD_SIZE.encode()
            self.received_image = bytearray()
            self.update_state = "awaiting_body"
            return self._consume_body(extra)
        return self._consume_body(tx)

    def _consume_body(self, chunk: bytes) -> bytes:
        if not chunk:
            return b""
        self.received_image.extend(chunk)
        if len(self.received_image) < self.expected_size:
            return b""
        body = bytes(self.received_image[:self.expected_size])
        tail = bytes(self.received_image[self.expected_size:])
        self.received_image = bytearray()
        self.update_state = "cli"
        crc = zlib.crc32(body) & 0xFFFFFFFF
        if crc != self.expected_crc and not self.accept_bad_crc:
            return SIM_BAD_CRC.encode()
        self.flash_success = True
        if self.new_version:
            self.version_str = self.new_version
        out = (SIM_VERIFIED_TPL % self.expected_size).encode()
        if tail:
            out.extend(self._consume_text(tail))
        return out
