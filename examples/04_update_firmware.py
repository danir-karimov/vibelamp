#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Пример 4. Обновление прошивки (OTA).

IAP-протокол ``update`` по USB CDC с фолбэком в USB DFU. Порт на время
прошивки захватывается (lease), поэтому фоновые потоки не должны конкурировать.

Запуск:
    python 04_update_firmware.py path/to/vibelamp.bin
    python 04_update_firmware.py path/to/vibelamp.bin --strategy iap
    python 04_update_firmware.py --sim        # без железа (синтетический образ)

Коды возврата: 0 - успех, иначе - ошибка (см. UpdateResult.success).
"""

from __future__ import annotations

import argparse
import os
import sys

from _common import add_common_args, open_lamp

#: Типовой путь собранного образа (firmware/build/Debug/vibelamp.bin).
_DEFAULT_BIN = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.pardir, os.pardir, os.pardir, "firmware", "build", "Debug", "vibelamp.bin"))


def _on_progress(event) -> None:
    sys.stdout.write("\r%-10s %5.1f%% %s" % (event.phase, event.percent, event.message))
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Обновление прошивки VibeLamp")
    add_common_args(parser)
    parser.add_argument("image", nargs="?", default=_DEFAULT_BIN,
                        help="путь к vibelamp.bin (по умолчанию build/Debug)")
    parser.add_argument("--strategy", default="auto", choices=["auto", "iap", "dfu"],
                        help="auto (IAP->DFU) | iap | dfu")
    args = parser.parse_args()

    if args.sim:
        image = bytes(1024)          # синтетический образ для эмулятора
    else:
        image = args.image
        if not os.path.isfile(image):
            sys.stderr.write("образ не найден: %s\n" % image)
            return 1

    lamp = open_lamp(args, timeout=2.0)
    try:
        before = str(lamp.firmware_version or "?")
        print("before  :", before)
        print("image   :", "<sim 1024 байт>" if args.sim else image)
        result = lamp.update_firmware(image, strategy=args.strategy,
                                      on_progress=_on_progress)
    finally:
        lamp.close()

    sys.stdout.write("\n")
    print("strategy:", result.strategy)
    print("crc32   : 0x%08X" % result.crc32)
    print("sent    : %d байт" % result.bytes_sent)
    print("after   :", result.version_after or "?")
    print("SUCCESS" if result.success else "FAILED: " + result.message)
    return 0 if result.success else 4


if __name__ == "__main__":
    raise SystemExit(main())
