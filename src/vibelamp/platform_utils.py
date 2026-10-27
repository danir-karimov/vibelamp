"""Платформенные нюансы доступа к USB CDC порту (Windows / Linux / macOS).

Здесь сосредоточены все «грязные» детали ОС, чтобы остальной SDK был
платформо-независимым:

* **Windows** — порты ``COM10`` и выше для Win32 API требуют префикса ``\\\\.\\``;
* **macOS** — открывать нужно ``/dev/cu.*``, а не ``/dev/tty.*`` (иначе
  открытие блокируется, пока не поднят CTS);
* **Linux** — ``/dev/ttyACM0`` принадлежит группе ``dialout``: без членства в
  группе или udev-правила открытие даёт ``PermissionError``; для DFU-режима
  (``0483:df11``) нужно отдельное правило.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Iterable, List

__all__ = [
    "current_os",
    "normalize_port",
    "prefer_serial_paths",
    "udev_rule",
    "udev_install_instructions",
    "permission_hint",
    "is_busy_error",
    "is_dfu_id",
    "DFU_VID",
    "DFU_PID",
]

#: USB DFU-идентификаторы ST System Bootloader (после команды ``dfu``).
DFU_VID = 0x0483
DFU_PID = 0xDF11

_WIN_COM_RE = re.compile(r"^\\\\\?\\?\\?(COM(\d+))$", re.IGNORECASE)
_WIN_COM_PLAIN_RE = re.compile(r"^COM(\d+)$", re.IGNORECASE)


def current_os() -> str:
    """Вернуть каноническое имя ОС: ``windows`` | ``linux`` | ``darwin``."""
    name = getattr(sys, "platform", "") or os.name
    if name.startswith("win"):
        return "windows"
    if name.startswith("darwin"):
        return "darwin"
    if name.startswith("linux"):
        return "linux"
    return name


def normalize_port(path: str) -> str:
    """Привести путь порта к виду, пригодному для ``pyserial``.

    ``COM10`` и выше на Windows требуют префикса пространства имён Win32.
    """
    path = (path or "").strip()
    if current_os() != "windows":
        return path
    m = _WIN_COM_PLAIN_RE.match(path)
    if m:
        return path if int(m.group(1)) < 10 else f"\\\\.\\{path}"
    m = _WIN_COM_RE.match(path)
    if m and int(m.group(2)) >= 10 and not path.startswith("\\\\.\\"):
        return f"\\\\.\\COM{m.group(2)}"
    return path


def prefer_serial_paths(paths: Iterable[str]) -> List[str]:
    """Отфильтровать дубли: на macOS для каждого устройства существуют
    ``/dev/tty.usbmodemX`` и ``/dev/cu.usbmodemX`` — используем ``cu``.

    На других ОС список возвращается без изменений (порядок сохранён).
    """
    items = list(paths)
    if current_os() != "darwin":
        return items
    cu = {p for p in items if p.startswith("/dev/cu.")}
    result: List[str] = []
    for p in items:
        if p.startswith("/dev/tty.") and p.replace("/dev/tty.", "/dev/cu.", 1) in cu:
            continue
        result.append(p)
    return result


def is_dfu_id(vid: int | None, pid: int | None) -> bool:
    """``True``, если USB-идентификаторы соответствуют ST DFU Bootloader."""
    return vid == DFU_VID and pid == DFU_PID


#: Признаки «порт занят / доступ запрещён» в сообщениях разных ОС и локалей:
#: pyserial пробрасывает системное сообщение как есть, поэтому полагаться
#: только на английский текст нельзя.
_BUSY_NEEDLES = ("access is denied", "access denied", "permission", "busy",
                 "used by another", "busy or disconnected",
                 "занят", "отказано в доступе", "используется другим")


def is_busy_error(exc: BaseException) -> bool:
    """Похожа ли ошибка открытия порта на «занято / нет доступа»."""
    if isinstance(exc, PermissionError):
        return True
    if getattr(exc, "errno", None) == 13:
        return True
    text = str(exc).lower()
    return any(needle in text for needle in _BUSY_NEEDLES)


def udev_rule() -> str:
    """Текст udev-правил для VCP и DFU-режима (Linux)."""
    return (
        "# /etc/udev/rules.d/99-vibelamp.rules\n"
        'SUBSYSTEM=="tty", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="5740", '
        'MODE="0666", TAG+="uaccess"\n'
        'SUBSYSTEM=="usb", ATTR{idVendor}=="0483", ATTR{idProduct}=="df11", '
        'MODE="0666", TAG+="uaccess"\n'
    )


def udev_install_instructions() -> str:
    """Пошаговая установка правил (Linux)."""
    return (
        "1. sudo cp 99-vibelamp.rules /etc/udev/rules.d/\n"
        "2. sudo udevadm control --reload-rules && sudo udevadm trigger\n"
        "3. переподключите USB-кабель\n"
        "(альтернатива без правил: sudo usermod -aG dialout $USER и перелогин)"
    )


def permission_hint(path: str = "") -> str:
    """Подсказка пользователю об отказе доступа — зависит от ОС."""
    where = f" ({path})" if path else ""
    system = current_os()
    if system == "linux":
        return ("Linux: нет прав на порт" + where +
                ". Установите udev-правила: `vibelamp udev` "
                "(или добавьте пользователя в группу dialout)")
    if system == "darwin":
        return ("macOS: убедитесь, что открывается /dev/cu.* (не /dev/tty.*), "
                "и что порт не занят другим процессом")
    return ("Windows: порт занят другим приложением (терминал, VS Code, "
            "STM32CubeProgrammer) либо драйвер CDC не установлен")
