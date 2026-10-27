"""Иерархия ошибок VibeLamp SDK.

Все ошибки наследуются от :class:`VibeLampError`, поэтому потребитель может
перехватывать как весь SDK (``except VibeLampError``), так и конкретный класс.

Каждая ошибка может нести атрибут ``hint`` — человекочитаемую подсказку по
устранению (права доступа на Linux, конфликт драйвера на Windows и т. п.).
"""

from __future__ import annotations

__all__ = [
    "VibeLampError",
    "DiscoveryError",
    "DeviceNotFoundError",
    "TransportError",
    "PortNotFoundError",
    "PortBusyError",
    "PermissionDeniedError",
    "ConnectionLostError",
    "ProtocolError",
    "DeviceTimeoutError",
    "UnexpectedResponseError",
    "IncompatibleVersionError",
    "ValidationError",
    "FirmwareUpdateError",
    "DeviceStuckError",
    "FlashFailedError",
    "RebootTimeoutError",
]


class VibeLampError(Exception):
    """Базовая ошибка SDK.

    :param message: текст ошибки.
    :param hint: необязательная подсказка по устранению проблемы.
    """

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message if hint is None else f"{message} | {hint}")
        self.message = message
        self.hint = hint


# --------------------------------------------------------------------------- #
# Обнаружение устройств
# --------------------------------------------------------------------------- #
class DiscoveryError(VibeLampError):
    """Ошибка этапа поиска устройств."""


class DeviceNotFoundError(DiscoveryError):
    """VibeLamp не найден среди подключённых устройств."""

    def __init__(self, message: str = "VibeLamp (VID 0483, PID 5740) не найден",
                 hint: str | None = None) -> None:
        super().__init__(message, hint)


# --------------------------------------------------------------------------- #
# Транспорт (последовательный порт)
# --------------------------------------------------------------------------- #
class TransportError(VibeLampError):
    """Ошибка транспортного уровня (открытие/чтение/запись порта)."""


class PortNotFoundError(TransportError):
    """Указанный порт отсутствует в системе."""


class PortBusyError(TransportError):
    """Порт уже занят другим процессом (IDE, терминал, STM32_Programmer_CLI)."""


class PermissionDeniedError(TransportError):
    """Недостаточно прав для открытия порта (группа ``dialout`` / udev-правило)."""


class ConnectionLostError(TransportError):
    """Соединение разорвано во время работы (выдернут кабель, ребут устройства)."""


# --------------------------------------------------------------------------- #
# Протокол CLI
# --------------------------------------------------------------------------- #
class ProtocolError(VibeLampError):
    """Нарушение текстового CLI-протокола."""


class DeviceTimeoutError(ProtocolError):
    """Устройство не ответило в отведённое время."""


class UnexpectedResponseError(ProtocolError):
    """Получен ответ, не соответствующий ожидаемому формату."""


class DeviceRejectedError(ProtocolError):
    """Устройство отклонило команду (прошивка отвечает ``Error: ...``)."""


class IncompatibleVersionError(ProtocolError):
    """Протокол/прошивка устройства несовместима с SDK."""


# --------------------------------------------------------------------------- #
# Валидация параметров команд
# --------------------------------------------------------------------------- #
class ValidationError(VibeLampError, ValueError):
    """Параметр команды вне допустимого диапазона (отлавливается до отправки)."""


# --------------------------------------------------------------------------- #
# Обновление прошивки
# --------------------------------------------------------------------------- #
class FirmwareUpdateError(VibeLampError):
    """Ошибка процесса обновления прошивки."""


class DeviceStuckError(FirmwareUpdateError):
    """Устройство застряло в режиме передачи образа (не отвечает на CLI)."""


class FlashFailedError(FirmwareUpdateError):
    """Устройство отвергло образ (CRC/таблица векторов не прошли верификацию)."""


class RebootTimeoutError(FirmwareUpdateError):
    """Устройство не вернулось в CLI после перезагрузки."""
