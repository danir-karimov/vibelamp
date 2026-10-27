# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [SemVer](https://semver.org/lang/ru/).

## [0.1.0] — 2026-10-28

Первый публичный релиз: библиотечное ядро SDK без внешних зависимостей,
кроме `pyserial`.

### Added

- **discovery** — `scan()`, `find_first()`, `watch()`, `diagnose()`; автопоиск
  устройства по USB VID:PID `0483:5740` с handshake-командой `version` и
  распознаванием баннера загрузки прошивки.
- **transport** — `Transport` (ABC), `SerialTransport` (USB CDC 115200 8N1) и
  `LoopbackTransport` для тестов и эмулятора.
- **protocol / effects / device** — транзактор запрос-ответ, типизированные
  эффекты (`Effect.rainbow(...)`, `set_rgb`, `set_brightness`), состояния
  ИИ-агентов через `Lamp.set_mode(AgentState...)`, `status()`, `raw()`, `batch()`.
- **ota** — обновление прошивки по IAP-протоколу `update` (заголовок `A5 5A` +
  CRC32) с фолбэком в USB DFU и recovery-последовательностью.
- **simulator** — `LampSimulator`: эмулятор прошивки для тестов и примеров без
  реального устройства (`--sim`).
- **platform_utils** — особенности портов по платформам (`/dev/cu.*` vs
  `/dev/tty.*`, `\\.\COM10+` на Windows, udev-правила для Linux).
- **errors** — типизированная иерархия ошибок с подсказками по платформам.
- Примеры `examples/01…04` (discovery, команды, эффекты, обновление прошивки)
  и 13 модулей тестов, полностью покрывающих работу без железа.
- Спецификация CLI-протокола в репозитории: `docs/protocol.md`.

### Notes

- Подпись прошивки (Ed25519) и отдельная консоль `vibelamp` в объём SDK не
  входят: целостность образа проверяется CRC32.
