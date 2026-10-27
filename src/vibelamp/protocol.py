"""Транзактор ASCII CLI-протокола VibeLamp.

Прошивка VibeLamp (её парсер CLI) отвечает так:

* управляющие команды -> ``OK`` или ``OK: <пояснение>``;
* неверные аргументы -> ``Error: invalid arguments. <usage>``;
* запросы -> многострочный текст (``version``, ``status``, ``help``);
* включённое локальное эхо (по умолчанию) возвращает введённую строку,
  поэтому эхо-дубликаты отбрасываются при разборе ответа.

Клиент однопоточен на одну транзакцию (один «in-flight» запрос): текстовый
протокол не имеет идентификаторов запроса, поэтому параллельные команды
разрешены только через блокировку.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .errors import (DeviceRejectedError, DeviceTimeoutError,
                     UnexpectedResponseError)
from .transport import MAX_LINE_LEN, Transport
from .versioning import FirmwareVersion, parse_version

__all__ = ["ProtocolClient", "StatusSnapshot", "parse_status"]

_COLOR_RE = re.compile(r"^color:\s*rgb\(\s*(\d+)\s+(\d+)\s+(\d+)\s*\)", re.IGNORECASE)
_EFFECT_RE = re.compile(r"^effect:\s*([A-Za-z0-9_]+)", re.IGNORECASE)
_PARAMS_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_BRIGHTNESS_RE = re.compile(r"^brightness:\s*(\d+)", re.IGNORECASE)
_UPTIME_RE = re.compile(r"^uptime:\s*(\d+)", re.IGNORECASE)

#: Признаки того, что прошивка «на связи»: ответ на ``version`` и баннер,
#: который МК печатает сразу после (пере)загрузки при открытии CDC-порта.
_READY_MARKERS = (b"version:", b"Welcome to CLI VibeLamp")


@dataclass
class StatusSnapshot:
    """Разобранный ответ команды ``status``."""

    version: Optional[FirmwareVersion] = None
    effect: Optional[str] = None
    params: Tuple[int, int] = (0, 0)
    rgb: Tuple[int, int, int] = (0, 0, 0)
    brightness: Optional[int] = None
    uptime_ms: Optional[int] = None
    raw: str = ""

    @property
    def is_on(self) -> bool:
        """Лампа считается включённой, если яркость и цвет ненулевые."""
        return bool(self.brightness) and any(self.rgb)

    def to_dict(self) -> dict:
        return {
            "version": str(self.version) if self.version else None,
            "effect": self.effect,
            "params": list(self.params),
            "rgb": list(self.rgb),
            "brightness": self.brightness,
            "uptime_ms": self.uptime_ms,
        }


def parse_status(text: str) -> StatusSnapshot:
    """Разобрать многострочный ответ ``status``.

    Формат прошивки::

        version: 1.0.25
        effect: wave (params: 3500 / 0)
        color: rgb(0 140 255)
        brightness: 180
        uptime: 41234 ms
    """
    snap = StatusSnapshot(raw=text or "")
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("version:"):
            snap.version = parse_version(line)
            continue
        m = _EFFECT_RE.match(line)
        if m:
            snap.effect = m.group(1).lower()
            p = _PARAMS_RE.search(line)
            if p:
                snap.params = (int(p.group(1)), int(p.group(2)))
            continue
        m = _COLOR_RE.match(line)
        if m:
            snap.rgb = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            continue
        m = _BRIGHTNESS_RE.match(line)
        if m:
            snap.brightness = int(m.group(1))
            continue
        m = _UPTIME_RE.match(line)
        if m:
            snap.uptime_ms = int(m.group(1))
    return snap


class ProtocolClient:
    """Клиент CLI-протокола поверх :class:`~vibelamp.transport.Transport`."""

    def __init__(self, transport: Transport, timeout: float = 1.0,
                 retries: int = 1, quiet_ms: float = 150.0,
                 disable_echo: bool = True, readiness_timeout: float = 1.5) -> None:
        self.transport = transport
        self.timeout = float(timeout)
        self.retries = max(0, int(retries))
        self.quiet_ms = max(20.0, float(quiet_ms)) / 1000.0
        self.disable_echo = disable_echo
        self.readiness_timeout = float(readiness_timeout)
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- lifecycle
    @property
    def is_open(self) -> bool:
        return self.transport.is_open

    def open(self) -> None:
        """Открыть канал и привести устройство к «чистому» диалогу."""
        if not self.transport.is_open:
            self.transport.open()
        # Открытие CDC-порта может перезапускать МК: ждём баннер загрузки или
        # ответ на `version`, иначе первая команда потеряется в boot-окне.
        self.wait_ready(self.readiness_timeout)
        self.transport.reset_input()
        if self.disable_echo:
            try:
                self.command("echo off", timeout=1.0, retries=0)
            except (DeviceTimeoutError, DeviceRejectedError, UnexpectedResponseError):
                # Прошивки без команды `echo` или шум на линии: молча
                # продолжаем — эхо отфильтровывается разбором ответов.
                pass

    def wait_ready(self, timeout: float = 1.5) -> bool:
        """Дождаться, пока прошивка выйдет на связь (баннер или ответ)."""
        if self.transport.read_until_any(_READY_MARKERS, 0.2):
            return True
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            self.transport.write_line("version")
            if self.transport.read_until_any(_READY_MARKERS, 0.3):
                return True
        return False

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> "ProtocolClient":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ---------------------------------------------------------------- internals
    #: Префиксы строк, которые прошивка реально использует в ответах на
    #: управляющие команды. Всё остальное (баннер загрузки, посимвольное эхо,
    #: «хвосты» прошлых команд) считается шумом и пропускается.
    _REPLY_PREFIXES = ("ok", "error", "version:", "effect:", "color:",
                       "brightness:", "uptime:", "===")

    #: Строки, которые устройством в ответ не считаются (баннер при открытии
    #: порта). Для многострочных запросов отбрасываются только они.
    _NOISE_PREFIXES = ("welcome to cli vibelamp",)

    @staticmethod
    def _is_echo(line: str, sent: str) -> bool:
        """Совпадает ли строка ответа с отправленной командой (локальное эхо)."""
        return line.strip().lower() == sent.strip().lower()

    @classmethod
    def _looks_like_reply(cls, line: str) -> bool:
        """Похожа ли строка на ответ прошивки, а не на шум/эхо/баннер."""
        low = line.strip().lower()
        return any(low.startswith(prefix) for prefix in cls._REPLY_PREFIXES)

    @classmethod
    def _is_noise(cls, line: str) -> bool:
        low = line.strip().lower()
        return any(low.startswith(prefix) for prefix in cls._NOISE_PREFIXES)

    def _read_first_reply(self, sent: str, deadline: float) -> Optional[str]:
        """Первая осмысленная строка ответа (без эха, баннера и мусора).

        Пустая строка (например, эхо пробела) не означает конец ответа —
        выход только по реальному таймауту чтения.
        """
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            raw = self.transport.read_until(b"\n", remaining)
            if not raw:
                return None
            line = raw.decode("latin-1", "replace").strip()
            if not line or self._is_echo(line, sent) or self._is_noise(line):
                continue
            if not self._looks_like_reply(line):
                continue
            return line

    def _classify(self, sent: str, reply: str) -> str:
        """Проверить, что ответ — успех (``OK``), иначе бросить ошибку."""
        low = reply.lower()
        if low.startswith("ok"):
            return reply
        if "error" in low:
            raise DeviceRejectedError(f"Устройство отклонило '{sent}': {reply}")
        raise UnexpectedResponseError(
            f"Неожиданный ответ на '{sent}': {reply}",
            "ожидался ответ, начинающийся с 'OK'")

    # ----------------------------------------------------------------- commands
    def command(self, line: str, timeout: Optional[float] = None,
                retries: Optional[int] = None) -> str:
        """Отправить управляющую команду и дождаться ``OK``.

        :raises DeviceTimeoutError: устройство молчит после всех повторов.
        :raises DeviceRejectedError: прошивка ответила ``Error: ...``.
        """
        line = (line or "").strip()
        if not line:
            raise ValueError("пустая команда")
        if len(line) > MAX_LINE_LEN:
            raise ValueError(
                f"длина команды {len(line)} > {MAX_LINE_LEN} (лимит буфера прошивки)")
        budget = self.timeout if timeout is None else float(timeout)
        attempts = self.retries if retries is None else max(0, int(retries))

        with self._lock:
            last_error: Optional[DeviceTimeoutError] = None
            for _ in range(attempts + 1):
                self.transport.reset_input()
                self.transport.write_line(line)
                reply = self._read_first_reply(line, time.monotonic() + budget)
                if reply is None:
                    last_error = DeviceTimeoutError(
                        f"Нет ответа на '{line}' за {budget:.2f} с",
                        "проверьте подключение: не занят ли порт, жив ли кабель")
                    continue
                return self._classify(line, reply)
            raise last_error  # type: ignore[misc]

    def query(self, line: str, timeout: Optional[float] = None) -> List[str]:
        """Отправить запрос и собрать многострочный ответ.

        Конец блока определяется «тишиной» (:attr:`quiet_ms`): формат
        прошивки не имеет явного терминатора конца ответа.
        """
        line = (line or "").strip()
        budget = self.timeout if timeout is None else float(timeout)
        with self._lock:
            self.transport.reset_input()
            self.transport.write_line(line)
            lines: List[str] = []
            while True:
                raw = self.transport.read_until(
                    b"\n", budget if not lines else self.quiet_ms)
                if not raw:
                    break                      # тишина => блок ответа закончен
                text = raw.decode("latin-1", "replace").strip()
                if not text or self._is_echo(text, line) or self._is_noise(text):
                    continue
                lines.append(text)
            if not lines:
                raise DeviceTimeoutError(f"Пустой ответ на '{line}'")
            return lines

    # -------------------------------------------------------------- high level
    def version(self) -> FirmwareVersion:
        """Версия прошивки устройства (команда ``version``)."""
        text = "\n".join(self.query("version"))
        version = parse_version(text)
        if version is None:
            raise UnexpectedResponseError(f"Не удалось разобрать версию: {text!r}")
        return version

    def status(self) -> StatusSnapshot:
        """Текущее состояние устройства (команда ``status``)."""
        return parse_status("\n".join(self.query("status")))

    def help_text(self) -> str:
        """Полная текстовая справка прошивки (команда ``help``)."""
        return "\n".join(self.query("help", timeout=2.0))

    def set_echo(self, enabled: bool) -> str:
        """Включить/выключить локальное эхо ввода в VCP."""
        return self.command("echo on" if enabled else "echo off")
