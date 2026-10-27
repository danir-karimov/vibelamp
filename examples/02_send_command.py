#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Пример 2. Отправка команд в прошивку.

  * lamp.version()      - версия прошивки (команда ``version``);
  * lamp.status()       - разбор ответа ``status`` в StatusSnapshot;
  * lamp.raw("...")     - произвольная CLI-строка (escape hatch);
  * lamp.batch([...])   - последовательная отправка пакета команд;
  * lamp.help_text()    - справка прошивки.

Запуск:
    python 02_send_command.py            # первое найденное устройство
    python 02_send_command.py --port COM7
    python 02_send_command.py --sim      # без железа
"""

from __future__ import annotations

import argparse

from _common import add_common_args, open_lamp


def main() -> int:
    parser = argparse.ArgumentParser(description="Отправка команд VibeLamp")
    add_common_args(parser)
    args = parser.parse_args()

    lamp = open_lamp(args)
    try:
        print("version :", lamp.version())

        snap = lamp.status()
        print("status  :", snap.to_dict())
        print("is_on   :", snap.is_on)

        print("raw bri :", lamp.raw("bri 120"))

        replies = lamp.batch(["bri 200", "rgb 10 20 30", "stop"])
        print("batch   :", replies)

        print("help    :\n" + lamp.help_text())
    finally:
        lamp.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
