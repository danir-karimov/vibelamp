#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Пример 1. Поиск устройств VibeLamp.

  * scan()       - перечислить лампы с подтверждением командой ``version``;
  * find_first() - взять первую найденную или бросить DeviceNotFoundError;
  * diagnose()   - объяснить, почему кандидат не считается VibeLamp;
  * watch()      - hot-plug подписка (устройство появилось / исчезло).

Запуск:
    python 01_discovery.py        # реальные устройства на шине USB
    python 01_discovery.py --sim  # демо API на эмуляторе (без железа)
"""

from __future__ import annotations

import argparse
import time

from _common import add_common_args, LampSimulator, vibelamp


def demo_scan(opener=None, ports=None) -> None:
    print("== scan(): поиск всех VibeLamp ==")
    devices = vibelamp.scan(opener=opener, ports=ports)
    if not devices:
        print("  устройств не найдено (подключите лампу по USB)")
        return
    for dev in devices:
        print("  {path:<12} fw={version:<8} probed={probed} product={product!r}"
              .format(path=dev.path, version=dev.version_string,
                      probed=dev.probed, product=dev.product))


def demo_find_first() -> None:
    print("\n== find_first(): первая найденная лампа ==")
    try:
        dev = vibelamp.find_first()
    except vibelamp.DeviceNotFoundError as exc:
        print("  не найдено: %s" % exc.message)
        print("  подсказка: %s" % (exc.hint or "-"))
        return
    print("  -> %s (fw %s)" % (dev.path, dev.version_string))


def demo_diagnose() -> None:
    print("\n== diagnose(): почему кандидат не VibeLamp ==")
    rows = vibelamp.discovery.diagnose()
    if not rows:
        print("  портов с VID:PID 0483:5740 на шине нет")
        return
    for row in rows:
        print("  {path:<12} {status:<12} {error}".format(**row))


def demo_watch(seconds: float = 5.0) -> None:
    print("\n== watch(): hot-plug %.0f с (воткните/выдерните USB) ==" % seconds)
    with vibelamp.watch(
            on_added=lambda d: print("  + добавлено:", d.path),
            on_removed=lambda d: print("  - убрано:  ", d.path),
            interval=0.5):
        time.sleep(seconds)
    print("  подписка закрыта")


def main() -> int:
    parser = argparse.ArgumentParser(description="Поиск устройств VibeLamp")
    add_common_args(parser)
    args = parser.parse_args()

    if args.sim:
        sim = LampSimulator(version="1.0.32")
        # scan умеет работать с инъекцией транспорта: показываем handshake
        # на эмуляторе вместо реального перечисления COM-портов.
        demo_scan(opener=sim.opener(), ports=["SIM0"])
        print("\n(--sim: find_first/diagnose/watch работают с реальными "
              "портами и здесь пропущены)")
        return 0

    demo_scan()
    demo_find_first()
    demo_diagnose()
    demo_watch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
