"""Транспортный слой SDK: абстракция последовательного канала.

Вся остальная библиотека (``protocol``, ``device``, ``ota``) работает только
через :class:`Transport`, поэтому тесты и симулятор не требуют реального
железа: достаточно :class:`LoopbackTransport` с обработчиком команд.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import deque
from typing import Callable, Deque, Iterable, Optional

from .errors import (
    ConnectionLostError,
    PermissionDeniedError,
    PortBusyError,
    PortNotFoundError,
    TransportError,
)

__all__ = ["Transport", "SerialTransport", "LoopbackTransport"]

#: Максимальная длина строки CLI-буфера прошивки (docs/protocol.md).
MAX_LINE_LEN = 64


class Transport(ABC):
    """Абстрактный двунаправленный байтовый канал к светильнику."""

    #: Идентификатор канала для логов (путь порта или псевдоним).
    name: str = "<transport>"

    # ---------------------------------------------------------------- lifecycle
    @abstractmethod
    def open(self) -> None:
        """Открыть канал."""

    @abstractmethod
    def close(self) -> None:
        """Закрыть канал (идемпотентно)."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """``True``, если канал открыт."""

    def __enter__(self) -> "Transport":
        if not self.is_open:
            self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------- output
    @abstractmethod
    def write(self, data: bytes) -> int:
        """Записать байты в канал, вернуть количество записанных байт."""

    def write_line(self, line: str) -> int:
        """Записать текстовую CLI-команду с терминатором ``\\r\\n``."""
        payload = line.rstrip("\r\n").encode("ascii", "replace") + b"\r\n"
        return self.write(payload)

    # -------------------------------------------------------------------- input
    @abstractmethod
    def read_until(self, needle: bytes, timeout: float) -> bytes:
        """Читать до первого вхождения ``needle`` или истечения ``timeout``.

        Возвращает накопленные байты (включая ``needle``, если он пришёл).
        Таймаут — не ошибка: возвращается то, что успело накопиться.
        """

    @abstractmethod
    def read_available(self) -> bytes:
        """Неблокирующе прочитать всё, что есть в буфере приёма."""

    @abstractmethod
    def reset_input(self) -> None:
        """Сбросить буфер приёма (выбросить «мусор» после переподключения)."""

    def read_until_any(self, needles: Iterable[bytes], timeout: float) -> bytes:
        """Читать до появления любого из маркеров (или таймаута).

        Используется для ожидания готовности устройства: прошивка может
        сообщить о загрузке баннером либо ответить на отправленную команду.
        """
        markers = [n for n in needles if n]
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while True:
            chunk = self.read_available()
            if chunk:
                buf.extend(chunk)
                if any(marker in buf for marker in markers):
                    return bytes(buf)
            elif not self._wait(deadline):
                return bytes(buf)
            else:
                time.sleep(0.005)

    # ------------------------------------------------------------------ helpers
    def read_line(self, timeout: float) -> str:
        """Прочитать одну текстовую строку ответа (``\\n`` — терминатор)."""
        raw = self.read_until(b"\n", timeout)
        return raw.decode("latin-1", "replace").strip()

    @staticmethod
    def _wait(deadline: float) -> bool:
        """``True``, если срок ещё не истёк."""
        return time.monotonic() < deadline


class SerialTransport(Transport):
    """Транспорт поверх реального USB CDC VCP через ``pyserial``.

    Платформенные нюансы:

    * ``dsrdtr`` / ``rtscts`` принудительно выключены — иначе на macOS
      открытие блокируется из-за отсутствия аппаратного CTS;
    * скорость 115200 указана для совместимости, USB CDC её игнорирует;
    * ``write_timeout`` защищает от зависания при отключённом кабеле.
    """

    def __init__(self, port: str, baudrate: int = 115200,
                 read_timeout: float = 0.05, write_timeout: float = 2.0) -> None:
        self.name = port
        self.port = port
        self.baudrate = baudrate
        self.read_timeout = read_timeout
        self.write_timeout = write_timeout
        self._sp = None

    # ---------------------------------------------------------------- lifecycle
    def open(self) -> None:
        if self._sp is not None:
            return
        try:
            import serial  # локальный импорт: SDK импортируется и без порта
        except ImportError as exc:  # pragma: no cover - окружение без pyserial
            raise TransportError(
                "pyserial не установлен", "pip install pyserial>=3.5") from exc
        try:
            self._sp = serial.Serial(
                self.port,
                self.baudrate,
                timeout=self.read_timeout,
                write_timeout=self.write_timeout,
                dsrdtr=False,
                rtscts=False,
                xonxoff=False,
            )
        except Exception as exc:  # noqa: BLE001 - нормализуем pyserial-исключения
            raise self._translate(exc) from exc

    def close(self) -> None:
        sp, self._sp = self._sp, None
        if sp is None:
            return
        try:
            sp.close()
        except Exception:  # noqa: BLE001 - закрытие мёртвого порта не ошибка
            pass

    @property
    def is_open(self) -> bool:
        return self._sp is not None and bool(getattr(self._sp, "is_open", False))

    def _translate(self, exc: Exception) -> TransportError:
        """Преобразовать исключение pyserial в типизированную ошибку SDK."""
        import serial

        from .platform_utils import is_busy_error, permission_hint

        text = str(exc)
        low = text.lower()
        if isinstance(exc, serial.SerialException) and "could not open port" in low:
            if "cannot find the file" in low:
                return PortNotFoundError(f"Порт {self.port} не существует")
            if is_busy_error(exc):
                return PortBusyError(
                    f"Порт {self.port} недоступен", permission_hint(self.port))
            return PortBusyError(f"Не удалось открыть {self.port}", text)
        if isinstance(exc, OSError) and (getattr(exc, "errno", None) == 13
                                         or is_busy_error(exc)):
            return PermissionDeniedError(
                f"Нет прав на {self.port}", permission_hint(self.port))
        return TransportError(f"Ошибка порта {self.port}: {text}")

    # ------------------------------------------------------------------- output
    def write(self, data: bytes) -> int:
        if self._sp is None:
            raise ConnectionLostError(f"Порт {self.port} не открыт")
        try:
            n = self._sp.write(data)
            self._sp.flush()
            return int(n)
        except Exception as exc:  # noqa: BLE001
            raise ConnectionLostError(
                f"Запись в {self.port} не удалась (кабель отключён?)", str(exc)) from exc

    # -------------------------------------------------------------------- input
    def read_until(self, needle: bytes, timeout: float) -> bytes:
        if self._sp is None:
            raise ConnectionLostError(f"Порт {self.port} не открыт")
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while True:
            try:
                pending = self._sp.in_waiting
                if pending:
                    buf.extend(self._sp.read(pending))
                    if needle in buf:
                        break
                elif not self._wait(deadline):
                    break
                else:
                    time.sleep(0.005)
            except Exception as exc:  # noqa: BLE001
                raise ConnectionLostError(
                    f"Чтение из {self.port} прервано", str(exc)) from exc
        return bytes(buf)

    def read_available(self) -> bytes:
        if self._sp is None:
            return b""
        try:
            pending = self._sp.in_waiting
            return self._sp.read(pending) if pending else b""
        except Exception:  # noqa: BLE001
            return b""

    def reset_input(self) -> None:
        if self._sp is None:
            return
        try:
            self._sp.reset_input_buffer()
        except Exception:  # noqa: BLE001
            pass


class LoopbackTransport(Transport):
    """Транспорт-заглушка для тестов и симулятора.

    :param name: псевдоним канала для логов.
    :param handler: ``callable(tx: bytes) -> bytes | None`` — вызывается на
        каждую запись; возвращённые байты добавляются в буфер приёма. Так
        эмулятор прошивки реагирует на команды без реального устройства.
    :param initial_rx: байты, доступные для чтения сразу (например, баннер
        загрузки прошивки).

    Все записанные байты сохраняются в :attr:`tx_log`, а разобранные по
    строкам — в :attr:`tx_lines`: тесты проверяют ровно те CLI-команды,
    которые отправил SDK.
    """

    def __init__(self,
                 name: str = "loopback",
                 handler: Optional[Callable[[bytes], Optional[bytes]]] = None,
                 initial_rx: bytes = b"") -> None:
        self.name = name
        self.handler = handler
        self._rx: Deque[int] = deque(initial_rx)
        self._pending = bytearray()
        self._open = False
        #: Все записанные в канал байты (для ассертов в тестах).
        self.tx_log = bytearray()
        #: Записанные команды построчно, без терминаторов.
        self.tx_lines: list[str] = []

    # ---------------------------------------------------------------- lifecycle
    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    # ------------------------------------------------------------------- output
    def write(self, data: bytes) -> int:
        if not self._open:
            raise ConnectionLostError(f"Канал {self.name} не открыт")
        self.tx_log.extend(data)
        self._pending.extend(data)
        while b"\n" in self._pending:
            line, _, rest = bytes(self._pending).partition(b"\n")
            self._pending = bytearray(rest)
            self.tx_lines.append(line.decode("latin-1", "replace").rstrip("\r"))
        if self.handler is not None:
            reply = self.handler(data)
            if reply:
                self.feed(reply)
        return len(data)

    # -------------------------------------------------------------------- input
    def feed(self, data: bytes) -> None:
        """Положить байты в буфер приёма (используется симулятором)."""
        self._rx.extend(data)

    def read_until(self, needle: bytes, timeout: float) -> bytes:
        if not self._open:
            raise ConnectionLostError(f"Канал {self.name} не открыт")
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while True:
            while self._rx:
                buf.append(self._rx.popleft())
                if bytes(buf).endswith(needle):
                    return bytes(buf)
            if not self._wait(deadline):
                return bytes(buf)
            time.sleep(0.002)

    def read_available(self) -> bytes:
        data = bytes(self._rx)
        self._rx.clear()
        return data

    def reset_input(self) -> None:
        self._rx.clear()
