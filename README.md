# VibeLamp Python SDK

[![CI](https://github.com/danir-karimov/vibelamp/actions/workflows/ci.yml/badge.svg)](https://github.com/danir-karimov/vibelamp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

Кроссплатформенный (Windows / Linux / macOS) Python-пакет для светильника
**VibeLamp**: автоматическое обнаружение по USB, управление эффектами и
обновление прошивки по USB CDC без внешних утилит.

* Пакет: `vibelamp` (import `vibelamp`), Python ≥ 3.9, зависимость — только `pyserial`.
* Протокол: ASCII CLI 115200 8N1, USB VID:PID `0483:5740`
  (спецификация — [`docs/protocol.md`](docs/protocol.md)).
* Обновление: IAP-протокол `update` (заголовок `A5 5A` + CRC32) с фолбэком в USB DFU.
* Без железа: `LampSimulator` + loopback-транспорт — все примеры запускаются
  с флагом `--sim`, устройство не нужен.

## Установка

```powershell
# из исходников (клонировать репозиторий, затем из его корня)
python -m pip install -e ".[dev]"
pytest
```

Либо без установки пакета — примеры сами добавляют `src/` в `sys.path`,
см. [`examples/README.md`](examples/README.md).

## Быстрый старт

```python
import vibelamp

for dev in vibelamp.scan():                 # автопоиск + handshake `version`
    print(dev.path, dev.firmware_version)

with vibelamp.connect() as lamp:            # auto: первое найденное устройство
    lamp.set_rgb(255, 128, 0)
    lamp.effect(vibelamp.Effect.rainbow(speed_ms=40))
    lamp.update_firmware("vibelamp.bin", on_progress=print)
```

## Статус реализации (пошаговый план)

| Модуль | Назначение | Статус |
| :-- | :-- | :-- |
| `errors.py` | типизированная иерархия ошибок с подсказками по платформам | готово |
| `transport.py` | `Transport` ABC, `SerialTransport`, `LoopbackTransport` | готово |
| `platform_utils.py` | `cu`/`tty`, `\\.\COM10+`, udev-правила | этап 1 |
| `discovery.py` | `scan()`, `watch()`, probe-handshake | этап 1 |
| `protocol.py`, `effects.py`, `device.py` | транзактор, команды, `status()` | этап 2 |
| `simulator.py` | эмулятор прошивки для тестов без железа | этап 3 |
| `ota.py` | IAP + DFU + recovery | этап 4 |

> Область SDK намеренно сужена до библиотечного ядра (discovery / effects / ota
> поверх transport/protocol). Отдельная консоль `vibelamp` и подпись прошивки
> (Ed25519) из плана исключены — обновление проверяется только CRC32.

## Документация

* [`docs/protocol.md`](docs/protocol.md) — спецификация CLI-протокола и состояний.
* [`examples/README.md`](examples/README.md) — четыре готовых скрипта (поиск,
  команды, эффекты, обновление прошивки), все работают с `--sim` без устройства.
* [`tests/`](tests) — 13 модулей тестов; прогон: `pytest`.
* [`CHANGELOG.md`](CHANGELOG.md) — история изменений.

## Лицензия

MIT — см. [`LICENSE`](LICENSE).
