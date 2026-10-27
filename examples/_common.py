"""Общая обвязка примеров VibeLamp SDK.

Добавляет ``sdk/python/src`` в ``sys.path`` (ставить пакет pip'ом не нужно) и
даёт всем примерам одинаковые флаги ``--port`` / ``--sim``.
"""

from __future__ import annotations

import argparse
import os
import sys

_SRC = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src"))
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import vibelamp  # noqa: E402  (импорт после бутстрапа sys.path)
from vibelamp.simulator import LampSimulator  # noqa: E402

__all__ = ["vibelamp", "LampSimulator", "add_common_args", "open_lamp"]


def add_common_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Добавить общие флаги ``--port`` и ``--sim``."""
    parser.add_argument(
        "--port", metavar="PATH",
        help="путь порта (COM7 / /dev/ttyACM0 / /dev/cu.usbmodem1); "
             "по умолчанию - автопоиск по VID:PID 0483:5740")
    parser.add_argument(
        "--sim", action="store_true",
        help="работать с программным эмулятором прошивки вместо устройства")
    return parser


def open_lamp(args, **kwargs):
    """Вернуть открытый :class:`vibelamp.Lamp` (симулятор или устройство).

    С ``--sim`` поднимается :class:`~vibelamp.simulator.LampSimulator` и
    подключение идёт через его loopback-транспорт - железо не нужно.
    """
    if getattr(args, "sim", False):
        sim = LampSimulator(version="1.0.32", new_version="1.0.33")
        return vibelamp.connect(sim.attach(), **kwargs)
    return vibelamp.connect(getattr(args, "port", None), **kwargs)
