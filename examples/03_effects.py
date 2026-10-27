#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Пример 3. Управление эффектами.

  * lamp.set_rgb / set_brightness  - сплошной цвет и глобальная яркость;
  * Effect.<name>(...)             - типизированные эффекты с валидацией;
  * lamp.effect("rainbow 40")      - строка команды (escape hatch);
  * lamp.set_mode(AgentState...)   - высокоуровневые состояния ИИ-агента.

Запуск:
    python 03_effects.py            # первое найденное устройство
    python 03_effects.py --port COM7
    python 03_effects.py --sim      # без железа
"""

from __future__ import annotations

import argparse
import time

from _common import add_common_args, open_lamp
from vibelamp import AgentState, Effect


def main() -> int:
    parser = argparse.ArgumentParser(description="Управление эффектами VibeLamp")
    add_common_args(parser)
    parser.add_argument("--hold", type=float, default=1.0,
                        help="сколько секунд держать каждый эффект (по умолч. 1)")
    args = parser.parse_args()
    hold = args.hold

    lamp = open_lamp(args, max_brightness=200)
    try:
        lamp.set_brightness(180)
        print("яркость 180 (предел max_brightness=%d)" % lamp.max_brightness)

        lamp.set_rgb(255, 128, 0)
        print("rgb(255,128,0)"); time.sleep(hold)

        lamp.effect(Effect.rainbow(speed_ms=40))
        print("rainbow speed_ms=40"); time.sleep(hold)

        lamp.effect(Effect.wave(period_ms=3500, r=0, g=140, b=255))
        print("wave period_ms=3500 rgb(0,140,255)"); time.sleep(hold)

        lamp.effect(Effect.fire(cool=40, spark=200))
        print("fire cool=40 spark=200"); time.sleep(hold)

        lamp.effect("breathe 4000 255 0 120")   # строка без валидации
        print("breathe (строкой)"); time.sleep(hold)

        print("\n-- состояния ИИ-агента (mode) --")
        for state in AgentState:
            lamp.set_mode(state)
            print("  mode:", state.value)
            time.sleep(hold)

        lamp.stop()
        print("stop (заморозить кадр)")
        lamp.off()
        print("off")
    finally:
        lamp.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
