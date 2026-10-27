"""Высокоуровневый клиент светильника VibeLamp.

:class:`Lamp` — то, с чем работает приложение: типизированные команды,
валидация параметров, состояние и обновление прошивки. Низуровневые детали
(порт, транзактор, разбор ответов) скрыты в :mod:`vibelamp.transport` и
:mod:`vibelamp.protocol`.

::

    with vibelamp.connect() as lamp:      # автопоиск + handshake
        lamp.set_brightness(180)
        lamp.effect(Effect.wave(period_ms=3500, r=0, g=140, b=255))
        print(lamp.status().effect)
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import List, Optional, Sequence, Union

from .discovery import DeviceInfo, find_first
from .effects import AgentState, Effect, MAX_BRIGHTNESS, MODE_NAMES
from .errors import (ConnectionLostError, DeviceNotFoundError,
                     PermissionDeniedError, PortBusyError, ValidationError)
from .platform_utils import normalize_port
from .protocol import ProtocolClient, StatusSnapshot
from .transport import SerialTransport, Transport
from .versioning import FirmwareVersion

__all__ = ["Lamp"]

LampTarget = Union[DeviceInfo, str, Transport, None]


class Lamp:
    """Клиент одного светильника.

    :param device: :class:`~vibelamp.discovery.DeviceInfo`, путь порта или
        готовый :class:`~vibelamp.transport.Transport` (тесты/симулятор).
    :param timeout: бюджет ожидания ответа команды, с.
    :param retries: число повторов при таймауте.
    :param opener: фабрика транспорта (инъекция для тестов).
    :param brightness: рабочая яркость, применяемая при подключении.
    :param max_brightness: аппаратный предел яркости (ограничивает
        ``set_brightness`` на стороне хоста, как в расширении VS Code).
    """

    def __init__(self, device: LampTarget = None, *, timeout: float = 1.0,
                 retries: int = 1, opener=None, brightness: Optional[int] = None,
                 max_brightness: int = MAX_BRIGHTNESS,
                 baudrate: int = 115200, open_timeout: float = 1.5) -> None:
        self.info: Optional[DeviceInfo] = None
        self._opener = opener
        self._baudrate = baudrate
        self._open_timeout = float(open_timeout)
        self._brightness = self._check_level(brightness, "brightness") \
            if brightness is not None else None
        self._max_brightness = self._check_level(max_brightness, "max_brightness") \
            if max_brightness is not None else MAX_BRIGHTNESS
        self._firmware_version: Optional[FirmwareVersion] = None
        self._lease = threading.RLock()
        self._lease_owner: Optional[str] = None

        if isinstance(device, Transport):
            self._transport: Optional[Transport] = device
            self._direct = True
        elif isinstance(device, DeviceInfo):
            self.info = device
            self._transport = None
            self._direct = False
        elif isinstance(device, str) or device is None:
            self._transport = None
            self._direct = False
            if device is not None:
                self.info = DeviceInfo(path=device)
        else:
            raise ValidationError(
                f"некорректный аргумент device: {type(device).__name__}",
                "ожидается DeviceInfo, путь порта или Transport")

        self._explicit_path = getattr(self.info, "path", None)
        self._client = ProtocolClient(
            self._transport or _NullTransport(),
            timeout=timeout, retries=retries)

    # ---------------------------------------------------------------- internals
    @staticmethod
    def _check_level(value, name: str) -> int:
        try:
            ivalue = int(value)
        except (TypeError, ValueError):
            raise ValidationError(f"{name}: ожидалось целое число, получено {value!r}") from None
        if not 0 <= ivalue <= MAX_BRIGHTNESS:
            raise ValidationError(
                f"{name} = {ivalue} вне диапазона 0..{MAX_BRIGHTNESS}")
        return ivalue

    def _resolve_transport(self) -> Transport:
        """Вернуть транспорт, при необходимости найдя устройство."""
        if self._transport is not None and not isinstance(self._transport, _NullTransport):
            return self._transport
        if self._explicit_path:
            path = self._explicit_path
        else:
            self.info = find_first()
            path = self.info.path
        transport = (self._opener or (lambda p: SerialTransport(normalize_port(p), self._baudrate)))(path)
        self._transport = transport
        if self.info is None:
            self.info = DeviceInfo(path=path)
        self._client.transport = transport
        return transport

    # ---------------------------------------------------------------- lifecycle
    def open(self) -> "Lamp":
        """Подключиться: открыть порт, дождаться готовности, выключить эхо."""
        self._resolve_transport()
        deadline = time.monotonic() + self._open_timeout
        while True:
            try:
                self._client.open()
                break
            except (PortBusyError, PermissionDeniedError, ConnectionLostError) as exc:
                # Windows освобождает COM-порт с задержкой после завершения
                # предыдущего процесса — при частых вызовах CLI даём шанс.
                if time.monotonic() >= deadline:
                    raise
                self._client.close()
                time.sleep(0.2)
        try:
            self._firmware_version = self._client.version()
        except Exception:  # noqa: BLE001 - версия не критична для управления
            self._firmware_version = None
        if self._brightness is not None:
            self.set_brightness(self._brightness)
        return self

    def close(self) -> None:
        """Закрыть порт (идемпотентно)."""
        self._client.close()

    def __enter__(self) -> "Lamp":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self._client.is_open

    @property
    def path(self) -> Optional[str]:
        """Путь порта (``COM7`` / ``/dev/ttyACM0`` / ``/dev/cu.usbmodem1``)."""
        if self._transport is not None:
            return self._transport.name
        return self.info.path if self.info else None

    @property
    def firmware_version(self) -> Optional[FirmwareVersion]:
        """Версия прошивки, считанная при подключении."""
        return self._firmware_version

    @property
    def max_brightness(self) -> int:
        return self._max_brightness

    # ------------------------------------------------------------------ lease
    @contextmanager
    def lease(self, reason: str = "exclusive"):
        """Монопольно захватить устройство в рамках процесса.

        Нужно обновлению прошивки: пока lease активен, другие потоки не могут
        отправлять команды. Межпроцессная аренда — задача демона (этап 7
        плана); здесь защита только внутри одного процесса.
        """
        from .errors import PortBusyError

        acquired = self._lease.acquire(timeout=10.0)
        if not acquired:
            raise PortBusyError(
                "Устройство занято другой операцией",
                f"lease удерживается: {self._lease_owner or 'неизвестно'}")
        self._lease_owner = reason
        try:
            yield self
        finally:
            self._lease_owner = None
            self._lease.release()

    def _cmd(self, line: str) -> str:
        with self._lease:
            return self._client.command(line)

    # ------------------------------------------------------------- управление
    def on(self) -> str:
        """Включить светодиоды (восстановить последний цвет/эффект)."""
        return self._cmd("on")

    def off(self) -> str:
        """Погасить все светодиоды."""
        return self._cmd("off")

    def stop(self) -> str:
        """Остановить анимацию, заморозив последний кадр."""
        return self._cmd("stop")

    def set_rgb(self, r: int, g: int, b: int) -> str:
        """Сплошная заливка цветом (0..255 на канал)."""
        return self.effect(Effect("rgb", (r, g, b)))

    def set_brightness(self, value: int) -> str:
        """Глобальная яркость 0..255 с учётом :attr:`max_brightness`."""
        level = self._check_level(value, "brightness")
        limited = min(level, self._max_brightness)
        self._brightness = level
        return self._cmd(f"bri {limited}")

    def set_max_brightness(self, value: int) -> str:
        """Установить предел яркости и сразу применить его."""
        self._max_brightness = self._check_level(value, "max_brightness")
        current = self._brightness if self._brightness is not None else self._max_brightness
        return self.set_brightness(current)

    def effect(self, effect: Union[Effect, str, AgentState], *args) -> str:
        """Запустить эффект.

        Принимает готовый :class:`~vibelamp.effects.Effect`, строку CLI-команды
        (``"rainbow 40"`` — escape hatch без валидации) или
        :class:`~vibelamp.effects.AgentState`.
        """
        if isinstance(effect, AgentState):
            return self.set_mode(effect)
        if isinstance(effect, Effect):
            line = effect.to_command()
        elif isinstance(effect, str):
            line = effect.strip()
        else:
            raise ValidationError(
                f"некорректный эффект: {type(effect).__name__}",
                "ожидается Effect, строка команды или AgentState")
        return self._cmd(line)

    def set_mode(self, state: Union[AgentState, str]) -> str:
        """Высокоуровневое состояние ИИ-агента (пресет эффекта прошивки)."""
        name = state.value if isinstance(state, AgentState) else str(state).strip().lower()
        if name not in MODE_NAMES:
            raise ValidationError(
                f"неизвестное состояние '{name}'",
                f"доступно: {', '.join(sorted(MODE_NAMES))}")
        return self._cmd(f"mode {MODE_NAMES[name]}")

    # ----------------------------------------------------------------- чтение
    def status(self) -> StatusSnapshot:
        """Текущее состояние: версия, эффект, цвет, яркость, uptime."""
        with self._lease:
            return self._client.status()

    def version(self) -> FirmwareVersion:
        """Актуальная версия прошивки (запрос ``version``)."""
        with self._lease:
            self._firmware_version = self._client.version()
        return self._firmware_version

    def help_text(self) -> str:
        """Справка прошивки (команда ``help``) — полезна при отладке."""
        with self._lease:
            return self._client.help_text()

    def raw(self, line: str) -> str:
        """Отправить произвольную строку и вернуть ответ (escape hatch)."""
        line = (line or "").strip()
        if not line:
            raise ValueError("пустая команда")
        with self._lease:
            try:
                return self._client.command(line)
            except Exception:  # noqa: BLE001 - запросы отвечают не-строкой OK
                return "\n".join(self._client.query(line))

    def batch(self, commands) -> List[str]:
        """Выполнить команды строго по очереди, вернув их ответы.

        Прошивка **не** поддерживает конкатенацию через ``;`` (проверено по
        парсеру CLI прошивки VibeLamp), поэтому каждая команда отправляется отдельно.
        Первая же ошибка прерывает пакет: транзакций протокол не имеет.
        """
        replies: List[str] = []
        for item in commands:
            line = item.to_command() if isinstance(item, Effect) else str(item).strip()
            if not line:
                raise ValidationError("пустая команда в пакете")
            replies.append(self._cmd(line))
        return replies

    # ------------------------------------------------------------------ OTA
    def _reopener(self):
        """Фабрика транспорта для перечитывания версии после перезагрузки."""
        if self._direct:
            transport = self._transport

            def same() -> Transport:
                return transport
            return same

        path = self.path
        if not path:
            return None
        opener = self._opener
        baudrate = self._baudrate

        def make() -> Transport:
            transport = (opener or (lambda p: SerialTransport(normalize_port(p), baudrate)))(path)
            self._transport = transport
            self._client.transport = transport
            return transport
        return make

    def update_firmware(self, image, *, strategy: str = "auto",
                        on_progress=None, **kwargs):
        """Обновить прошивку: IAP по USB CDC с фолбэком в USB DFU.

        :param image: путь к ``vibelamp.bin`` или его содержимое (``bytes``).
        :param strategy: ``auto`` | ``iap`` | ``dfu``.
        :param on_progress: колбэк :class:`vibelamp.ota.UpdateProgress`.
        :return: :class:`vibelamp.ota.UpdateResult`.

        Порт на время прошивки захватывается через :meth:`lease`, поэтому
        фоновые потоки (например, визуализация состояний) не должны
        конкурировать с процессом записи.
        """
        from . import ota

        transport = self._resolve_transport()
        before = str(self._firmware_version) if self._firmware_version else None
        with self.lease("firmware update"):
            result = ota.update_firmware(
                image, transport=transport, strategy=strategy,
                on_progress=on_progress, version_before=before,
                reopener=self._reopener(), **kwargs)
        try:
            self._firmware_version = self._client.version()
        except Exception:  # noqa: BLE001 - версия уже подтверждена в ota.update_iap
            pass
        return result


class _NullTransport(Transport):
    """Занятая заглушка транспорта до момента реального подключения."""

    name = "<not connected>"

    def open(self) -> None:
        from .errors import ConnectionLostError
        raise ConnectionLostError("Транспорт ещё не разрешён")

    def close(self) -> None:
        return None

    @property
    def is_open(self) -> bool:
        return False

    def write(self, data: bytes) -> int:
        from .errors import ConnectionLostError
        raise ConnectionLostError("Транспорт ещё не разрешён")

    def read_until(self, needle: bytes, timeout: float) -> bytes:
        return b""

    def read_available(self) -> bytes:
        return b""

    def reset_input(self) -> None:
        return None
