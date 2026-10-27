"""VibeLamp — кроссплатформенный Python SDK для светильника VibeLamp.

Возможности (по мере зрелости модулей):

* :mod:`vibelamp.discovery` — автообнаружение устройств по USB VID/PID с
  подтверждением handshake-командой ``version`` и hot-plug подпиской;
* :mod:`vibelamp.device` — типизированное управление (цвет, яркость, эффекты,
  пресеты состояний ИИ-агентов) поверх ASCII CLI-протокола;
* :mod:`vibelamp.ota` — обновление прошивки: собственный IAP-протокол ``update``
  с фолбэком в USB DFU и recovery-подсказками;
* :mod:`vibelamp.transport` — абстракция канала (реальный порт / loopback);
* :mod:`vibelamp.simulator` — программный эмулятор прошивки для тестов.

Пример::

    import vibelamp

    devices = vibelamp.scan()
    with vibelamp.connect(devices[0]) as lamp:
        lamp.set_rgb(255, 128, 0)
"""

from __future__ import annotations

__version__ = "0.1.0"

#: Версия ASCII CLI-протокола, на которую ориентирован SDK.
PROTOCOL_VERSION = 1

#: USB Identifiers устройства VibeLamp (STMicroelectronics Virtual COM Port).
USB_VID = 0x0483
USB_PID = 0x5740

#: Скорость порта; USB CDC её игнорирует, указана для совместимости.
DEFAULT_BAUDRATE = 115200

from .errors import (  # noqa: E402  (после констант — избегаем цикла импортов)
    VibeLampError,
    ConnectionLostError,
    DeviceNotFoundError,
    DeviceStuckError,
    DeviceTimeoutError,
    DiscoveryError,
    FlashFailedError,
    FirmwareUpdateError,
    IncompatibleVersionError,
    PermissionDeniedError,
    PortBusyError,
    PortNotFoundError,
    ProtocolError,
    RebootTimeoutError,
    TransportError,
    UnexpectedResponseError,
    ValidationError,
)
from .transport import (  # noqa: E402
    LoopbackTransport,
    SerialTransport,
    Transport,
)
from .versioning import FirmwareVersion, parse_version  # noqa: E402
from .effects import AgentState, Effect  # noqa: E402
from .protocol import ProtocolClient, StatusSnapshot, parse_status  # noqa: E402
from .device import Lamp  # noqa: E402
from .discovery import (  # noqa: E402
    DeviceInfo,
    LampWatcher,
    find_first,
    probe_version,
    scan,
    watch,
)


def connect(device=None, *, timeout: float = 1.0, retries: int = 1,
            brightness=None, max_brightness: int = 255,
            opener=None, baudrate: int = DEFAULT_BAUDRATE) -> Lamp:
    """Подключиться к светильнику и вернуть открытый :class:`~vibelamp.device.Lamp`.

    :param device: :class:`~vibelamp.discovery.DeviceInfo`, путь порта или
        готовый транспорт; ``None`` — автоматический поиск первого устройства.
    :raises DeviceNotFoundError: устройство не найдено (``device=None``).
    """
    lamp = Lamp(device, timeout=timeout, retries=retries, brightness=brightness,
                max_brightness=max_brightness, opener=opener, baudrate=baudrate)
    return lamp.open()

__all__ = [
    "__version__",
    "PROTOCOL_VERSION",
    "USB_VID",
    "USB_PID",
    "DEFAULT_BAUDRATE",
    # errors
    "VibeLampError",
    "ConnectionLostError",
    "DeviceNotFoundError",
    "DeviceStuckError",
    "DeviceTimeoutError",
    "DiscoveryError",
    "FlashFailedError",
    "FirmwareUpdateError",
    "IncompatibleVersionError",
    "PermissionDeniedError",
    "PortBusyError",
    "PortNotFoundError",
    "ProtocolError",
    "RebootTimeoutError",
    "TransportError",
    "UnexpectedResponseError",
    "ValidationError",
    # transport
    "LoopbackTransport",
    "SerialTransport",
    "Transport",
    # versioning
    "FirmwareVersion",
    "parse_version",
    # effects
    "AgentState",
    "Effect",
    # protocol
    "ProtocolClient",
    "StatusSnapshot",
    "parse_status",
    # device
    "Lamp",
    "connect",
    # discovery
    "DeviceInfo",
    "LampWatcher",
    "find_first",
    "probe_version",
    "scan",
    "watch",
]
