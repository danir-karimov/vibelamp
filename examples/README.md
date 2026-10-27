# Примеры использования VibeLamp Python SDK

Четыре самостоятельных скрипта: поиск устройства, отправка команд, управление
эффектами и обновление прошивки. Каждый работает и с реальным устройством, и с
программным эмулятором (`--sim`) - железо для запуска не нужен.

## Запуск

Ставить пакет pip'ом не обязательно: скрипты сами добавляют `../src` в `sys.path`.

```powershell
# реальное устройство (VID:PID 0483:5740) - автопоиск или явный порт
python 01_discovery.py
python 02_send_command.py --port COM7
python 03_effects.py
python 04_update_firmware.py .\vibelamp.bin   # .bin — образ прошивки VibeLamp

# без железа - на эмуляторе прошивки
python 01_discovery.py --sim
python 02_send_command.py --sim
python 03_effects.py --sim
python 04_update_firmware.py --sim
```

## Список примеров

| Файл | Тема | Ключевой API |
| :-- | :-- | :-- |
| `01_discovery.py` | Поиск устройства | `scan()`, `find_first()`, `diagnose()`, `watch()` |
| `02_send_command.py` | Отправка команд | `Lamp.version/status/raw/batch/help_text` |
| `03_effects.py` | Управление эффектами | `set_rgb`, `set_brightness`, `Effect.*`, `set_mode(AgentState)` |
| `04_update_firmware.py` | Обновление прошивки | `Lamp.update_firmware(strategy, on_progress)` |

## Общие флаги

- `--port PATH` - путь порта (`COM7` / `/dev/ttyACM0` / `/dev/cu.usbmodem1`); без него - автопоиск.
- `--sim` - работать с `LampSimulator` через loopback-транспорт (без устройства).

## Ссылки

- Спецификация CLI-протокола: [`../docs/protocol.md`](../docs/protocol.md)
- Обновление прошивки: `Lamp.update_firmware()` — IAP + фолбэк в USB DFU,
  пример `04_update_firmware.py` работает на эмуляторе (`--sim`).
- README SDK: [`../README.md`](../README.md)
