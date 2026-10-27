"""Обновление прошивки VibeLamp: IAP по USB CDC с фолбэком в USB DFU.

Протокол самопрошивки (сверен с ``firmware/Core/Src/update_flash.c`` и
``do_update.py``):

1. текстовая команда ``update`` -> прошивка стирает staging-слот Slot B и
   отвечает ``OK: update ready. Send 12-byte header then firmware.``;
2. 12-байтный заголовок (big-endian):
   ``[0]=0xA5 [1]=0x5A [2]=proto [3]=type(0=RAW) [4..7]=size [8..11]=crc32``;
3. тело образа блоками; USB CDC игнорирует baud rate, поэтому хост обязан
   **дозировать** поток под скорость записи Flash (буфер прошивки — 256 байт);
4. ответ ``OK: verified N bytes. Flashing, rebooting...`` -> копирование
   Slot B -> Slot A -> перезагрузка.

Отдельно реализован фолбэк в USB DFU (``dfu-util``) и текст recovery-процедуры
для «окирпичившегося» устройства.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import zlib
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .errors import (DeviceStuckError, DeviceTimeoutError, FlashFailedError,
                     FirmwareUpdateError, RebootTimeoutError, TransportError)
from .platform_utils import current_os, permission_hint
from .transport import Transport

__all__ = ["UpdateProgress", "UpdateResult", "build_header", "crc32",
           "cli_alive", "ensure_idle", "update_iap", "update_dfu",
           "update_firmware", "recovery_instructions", "dfu_available"]

HEADER_MAGIC = (0xA5, 0x5A)
HEADER_PROTO = 1
HEADER_TYPE_RAW = 0
HEADER_LEN = 12

#: Размер блока тела. Прошивка принимает не больше 256 байт за раз.
BODY_CHUNK = 64
#: Пауза между блоками: ~8 КБ/с — с запасом под скорость записи Flash.
BODY_PAUSE = 0.008

#: Максимальный размер образа (staging-слот).
MAX_IMAGE_SIZE = 128 * 1024

READY_MARKER = b"OK: update ready"
VERIFIED_MARKER = b"OK: verified"
ERROR_MARKER = b"Error"

ProgressCallback = Optional[Callable[["UpdateProgress"], None]]


@dataclass
class UpdateProgress:
    """Прогресс обновления для индикатора/лога."""

    phase: str
    sent: int = 0
    total: int = 0
    message: str = ""

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return round(100.0 * self.sent / self.total, 1)

    def to_dict(self) -> dict:
        return {"phase": self.phase, "sent": self.sent, "total": self.total,
                "percent": self.percent, "message": self.message}


@dataclass
class UpdateResult:
    """Итог попытки обновления прошивки."""

    success: bool
    strategy: str
    version_before: Optional[str] = None
    version_after: Optional[str] = None
    bytes_sent: int = 0
    crc32: int = 0
    message: str = ""

    def to_dict(self) -> dict:
        return {"success": self.success, "strategy": self.strategy,
                "version_before": self.version_before,
                "version_after": self.version_after,
                "bytes_sent": self.bytes_sent, "crc32": "0x%08X" % self.crc32,
                "message": self.message}


def crc32(data: bytes) -> int:
    """Отражённый CRC32 (zlib), совместимый с прошивкой."""
    return zlib.crc32(data) & 0xFFFFFFFF


def build_header(body: bytes, proto: int = HEADER_PROTO,
                 img_type: int = HEADER_TYPE_RAW) -> bytes:
    """Собрать 12-байтный заголовок образа."""
    if not body:
        raise FirmwareUpdateError("пустой образ прошивки")
    if len(body) > MAX_IMAGE_SIZE:
        raise FirmwareUpdateError(
            "образ %d байт превышает staging-слот %d байт" % (len(body), MAX_IMAGE_SIZE),
            "уменьшите образ или пересоберите прошивку без лишнего")
    return (bytes([HEADER_MAGIC[0], HEADER_MAGIC[1], proto & 0xFF, img_type & 0xFF])
            + len(body).to_bytes(4, "big")
            + crc32(body).to_bytes(4, "big"))


def _read_reply(transport: Transport, markers: List[bytes], timeout: float) -> bytes:
    """Читать до появления одного из маркеров или истечения таймаута."""
    buf = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunk = transport.read_available()
        if chunk:
            buf.extend(chunk)
            for marker in markers:
                if marker in buf:
                    return bytes(buf)
        else:
            time.sleep(0.005)
    return bytes(buf)


def cli_alive(transport: Transport, timeout: float = 0.35) -> bool:
    """Жив ли CLI: команда ``version`` должна вернуть ответ.

    Устройство, застрявшее в режиме приёма образа, молча проглатывает байты.
    Запрос ``version`` (а не пустая строка, как в ``do_update.py``) выбран
    специально: он не зависит от состояния локального эха, которое SDK
    выключает при подключении.
    """
    try:
        transport.reset_input()
        transport.write(b"version\r\n")
        return bool(transport.read_until(b"version:", timeout).strip())
    except TransportError:
        return False


def ensure_idle(transport: Transport, timeout: float = 20.0) -> bool:
    """Вернуть устройство в CLI-режим, если оно застряло в режиме прошивки.

    Как и ``do_update.py``: если CLI молчит, «докармливаем» устройство байтами,
    пока образ не наберёт ожидаемый размер — прошивка не найдёт заголовок,
    ошибётся на CRC и вернётся в CLI.

    :return: ``True``, если потребовалось восстановление.
    :raises DeviceStuckError: вернуть CLI не удалось.
    """
    if cli_alive(transport):
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            transport.write(b"\xff" * BODY_CHUNK)
        except TransportError as exc:
            raise DeviceStuckError(
                "Устройство недоступно при восстановлении", str(exc)) from exc
        time.sleep(BODY_PAUSE)
        reply = _read_reply(transport, [b"Error", b"OK"], 0.5)
        if reply:
            transport.reset_input()
            return True
    raise DeviceStuckError(
        "Не удалось вернуть устройство в CLI-режим",
        recovery_instructions())


def recovery_instructions() -> str:
    """Что делать, если прошивка повреждена и CLI не отвечает."""
    return ("Recovery: 1) зажмите BOOT0, нажмите и отпустите NRST, отпустите BOOT0; "
            "2) прошейте dfu-util: dfu-util -a 0 -s 0x08000000:leave -D vibelamp.bin "
            "(или scripts/flash_boot.bat)")


def dfu_available() -> bool:
    """Есть ли в PATH утилита ``dfu-util`` (кроссплатформенный DFU-прошивальщик)."""
    return shutil.which("dfu-util") is not None


def _confirm_version(transport: Transport, reopener=None,
                     timeout: float = 20.0) -> Optional[str]:
    """Считать версию после перезагрузки устройства."""
    from .versioning import parse_version

    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            if reopener is not None:
                transport.close()
                transport = reopener()
                transport.open()
            if not transport.is_open:
                transport.open()
            transport.reset_input()
            transport.write(b"version\r\n")
            head = transport.read_until(b"version:", 1.0)
            if b"version:" not in head:
                time.sleep(0.5)
                continue
            tail = transport.read_until(b"\n", 1.0)
            version = parse_version((head + tail).decode("latin-1", "replace"))
            if version is not None:
                return str(version)
        except (TransportError, OSError) as exc:  # порт ещё не перепоявился
            last_error = exc
        time.sleep(0.5)
    if last_error is not None:
        raise RebootTimeoutError(
            "Устройство не вернулось в CLI после прошивки", str(last_error))
    raise RebootTimeoutError(
        "Устройство не вернулось в CLI после прошивки", recovery_instructions())


def update_iap(transport: Transport, image: bytes, *,
               on_progress: ProgressCallback = None,
               ready_timeout: float = 9.0, verify_timeout: float = 30.0,
               reboot_timeout: float = 20.0, chunk: int = BODY_CHUNK,
               pause: float = BODY_PAUSE, version_before: Optional[str] = None,
               reopener=None, confirm: bool = True) -> UpdateResult:
    """Прошить образ по собственному IAP-протоколу через USB CDC.

    :param transport: открытый (или открываемый) транспорт устройства.
    :param image: содержимое ``vibelamp.bin``.
    :param on_progress: колбэк :class:`UpdateProgress`.
    :param reopener: фабрика транспорта для перечитывания версии после ребута
        (на реальном железе порт «исчезает» и появляется заново).
    :raises FlashFailedError: устройство не вошло в режим прошивки или
        отвергло образ (CRC/таблица векторов).
    :raises RebootTimeoutError: устройство не вернулось после перезагрузки.
    """
    body = bytes(image)
    digest = crc32(body)
    header = build_header(body)

    def emit(phase: str, sent: int = 0, message: str = "") -> None:
        if on_progress is not None:
            on_progress(UpdateProgress(phase=phase, sent=sent,
                                       total=len(body), message=message))

    if not transport.is_open:
        transport.open()

    emit("prepare", message="ожидание CLI-режима")
    if ensure_idle(transport):
        emit("prepare", message="устройство возвращено из режима прошивки")

    emit("staging", message="стирание staging-слота")
    transport.reset_input()
    transport.write(b"update\r\n")
    reply = _read_reply(transport, [READY_MARKER, ERROR_MARKER], ready_timeout)
    if READY_MARKER not in reply:
        raise FlashFailedError(
            "Устройство не вошло в режим прошивки: %s" % _tail(reply),
            "обновите прошивку до версии с поддержкой команды `update` "
            "или используйте стратегию strategy='dfu'")

    emit("transfer")
    transport.write(header)
    sent = 0
    while sent < len(body):
        end = min(sent + chunk, len(body))
        transport.write(body[sent:end])
        sent = end
        emit("transfer", sent)
        if pause:
            time.sleep(pause)

    emit("verify", len(body), "верификация образа")
    reply = _read_reply(transport, [VERIFIED_MARKER, ERROR_MARKER], verify_timeout)
    if VERIFIED_MARKER not in reply:
        raise FlashFailedError(
            "Устройство отвергло образ: %s" % _tail(reply),
            "проверьте, что образ собран для этой платы (vibelamp.bin)")

    emit("reboot", len(body), "перезагрузка")
    version_after = None
    if confirm:
        version_after = _confirm_version(transport, reopener=reopener,
                                         timeout=reboot_timeout)
    emit("done", len(body), "готово")
    return UpdateResult(success=True, strategy="iap",
                        version_before=version_before,
                        version_after=version_after,
                        bytes_sent=len(body), crc32=digest,
                        message="прошивка обновлена по IAP")


def _tail(reply: bytes, limit: int = 160) -> str:
    """Человекочитаемый хвост ответа устройства."""
    text = reply.decode("latin-1", "replace").replace("\r", " ").replace("\n", " ").strip()
    return (text[-limit:] or "<пусто>")


def load_image(image) -> bytes:
    """Принять образ как ``bytes`` или путь к файлу."""
    if isinstance(image, (bytes, bytearray, memoryview)):
        return bytes(image)
    with open(image, "rb") as handle:  # type: ignore[arg-type]
        return handle.read()


def update_dfu(image, *, alt: int = 0, address: str = "0x08000000",
               leave: bool = True, timeout: float = 180.0,
               version_before: Optional[str] = None,
               dfu_util: Optional[str] = None) -> UpdateResult:
    """Прошить образ через ``dfu-util`` (USB DFU, ``0483:DF11``).

    Используется как фолбэк, когда CLI/IAP недоступны. Требует установленный
    ``dfu-util`` (Linux: ``apt install dfu-util``, macOS: ``brew install dfu-util``,
    Windows: пакет dfu-util из WinLibs / ST CubeProgrammer).

    :raises FirmwareUpdateError: ``dfu-util`` не найден в PATH.
    :raises FlashFailedError: процесс прошивки завершился ошибкой.
    """
    import os
    import tempfile

    body = load_image(image)
    digest = crc32(body)
    binary = dfu_util or shutil.which("dfu-util")
    if binary is None:
        raise FirmwareUpdateError(
            "dfu-util не найден в PATH",
            "Linux: sudo apt install dfu-util | macOS: brew install dfu-util | "
            "Windows: распакуйте dfu-util в каталог из PATH. " + recovery_instructions())

    handle, temp_path = tempfile.mkstemp(suffix=".bin", prefix="vibelamp_")
    try:
        with os.fdopen(handle, "wb") as tmp:
            tmp.write(body)
        spec = "%s%s" % (address, ":leave" if leave else "")
        command = [binary, "-a", str(alt), "-s", spec, "-D", temp_path]
        try:
            completed = subprocess.run(command, capture_output=True, text=True,
                                       timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise FlashFailedError(
                "dfu-util не завершился за %.0f с" % timeout,
                "проверьте, что устройство в DFU (0483:DF11)" +
                ("" if current_os() != "linux" else "; нужно udev-правило на df11")
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise FlashFailedError(
                "dfu-util завершился с кодом %d: %s" % (completed.returncode, detail[-200:]),
                permission_hint() if "denied" in detail.lower() or "permission" in detail.lower()
                else recovery_instructions())
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    return UpdateResult(success=True, strategy="dfu",
                        version_before=version_before, version_after=None,
                        bytes_sent=len(body), crc32=digest,
                        message="прошивка записана через DFU")


def update_firmware(image, *, transport: Transport, strategy: str = "auto",
                    on_progress: ProgressCallback = None,
                    version_before: Optional[str] = None,
                    reopener=None, dfu_kwargs: Optional[dict] = None,
                    **iap_kwargs) -> UpdateResult:
    """Обновить прошивку выбранной стратегией.

    :param strategy: ``iap`` — только собственный протокол; ``dfu`` — только
        ``dfu-util``; ``auto`` — IAP, при неудаче DFU (если установлен).
    :param transport: транспорт устройства (для IAP).
    :param reopener: фабрика транспорта для перечитывания версии после ребута.
    """
    strategy = (strategy or "auto").lower()
    if strategy not in ("auto", "iap", "dfu"):
        raise FirmwareUpdateError(
            "неизвестная стратегия '%s'" % strategy, "ожидается auto|iap|dfu")
    body = load_image(image)

    if strategy == "dfu":
        return update_dfu(body, version_before=version_before,
                          **(dfu_kwargs or {}))

    try:
        return update_iap(transport, body, on_progress=on_progress,
                          version_before=version_before, reopener=reopener,
                          **iap_kwargs)
    except FirmwareUpdateError as exc:
        if strategy == "iap":
            raise
        if not dfu_available():
            raise FirmwareUpdateError(
                "IAP не удался (%s), а dfu-util не установлен" % exc.message,
                recovery_instructions()) from exc
        if on_progress is not None:
            on_progress(UpdateProgress(phase="dfu", message="фолбэк в DFU: " + exc.message))
        return update_dfu(body, version_before=version_before,
                          **(dfu_kwargs or {}))
