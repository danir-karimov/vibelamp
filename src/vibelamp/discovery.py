"""Автоматическое обнаружение светильников VibeLamp.

USB VID/PID ``0483:5740`` — это generic «STMicroelectronics Virtual COM
Port», который встречается у Nucleo/Discovery и других CDC-устройств, поэто
му одного фильтра по идентификаторам недостаточно. SDK дополнительно делает
**probe-handshake**: открывает порт, шлёт ``version`` и засчитывает
устройство как VibeLamp только если пришёл ответ ``version: X.Y.Z``.

Публичный API: :func:`scan`, :func:`watch`, :class:`DeviceInfo`.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional

from .errors import DeviceNotFoundError, TransportError
from .platform_utils import is_busy_error, normalize_port, prefer_serial_paths
from .transport import SerialTransport, Transport
from .versioning import FirmwareVersion, parse_version

__all__ = ["DeviceInfo", "scan", "find_first", "diagnose", "watch", "LampWatcher",
           "DEFAULT_VID", "DEFAULT_PID"]

log = logging.getLogger("vibelamp.discovery")

DEFAULT_VID = 0x0483
DEFAULT_PID = 0x5740

#: Ответ прошивки на команду ``version`` (см. docs/protocol.md).
_VERSION_MARKER = b"version:"
#: Баннер, который прошивка печатает сразу после загрузки. Открытие CDC-порта
#: на Windows может перезапускать МК, поэтому баннер — второй легитимный
#: признак «устройство живо» (версия есть и в нём: "... VibeLamp v1.0.32 ...").
_BANNER_MARKER = b"Welcome to CLI VibeLamp"
_READY_MARKERS = (_VERSION_MARKER, _BANNER_MARKER)


@dataclass(frozen=True)
class DeviceInfo:
    """Описание найденного устройства."""

    path: str
    vid: int = DEFAULT_VID
    pid: int = DEFAULT_PID
    serial_number: Optional[str] = None
    product: Optional[str] = None
    manufacturer: Optional[str] = None
    firmware_version: Optional[FirmwareVersion] = None
    #: ``True``, если устройство подтверждено handshake-командой ``version``.
    probed: bool = False

    @property
    def key(self) -> str:
        """Стабильный ключ для diff'а hot-plug событий."""
        return f"{self.path}|{self.serial_number or ''}"

    @property
    def version_string(self) -> str:
        return str(self.firmware_version) if self.firmware_version else "unknown"

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "vid": self.vid,
            "pid": self.pid,
            "serial_number": self.serial_number,
            "product": self.product,
            "manufacturer": self.manufacturer,
            "firmware_version": str(self.firmware_version) if self.firmware_version else None,
            "probed": self.probed,
        }


@dataclass
class _PortInfo:
    """Внутреннее представление записи из перечисления портов."""

    path: str
    vid: int = DEFAULT_VID
    pid: int = DEFAULT_PID
    serial_number: Optional[str] = None
    product: Optional[str] = None
    manufacturer: Optional[str] = None


def _default_opener(path: str) -> Transport:
    return SerialTransport(normalize_port(path))


def list_candidate_ports(vid: int = DEFAULT_VID, pid: int = DEFAULT_PID,
                         ports: Optional[Iterable[str]] = None) -> List[_PortInfo]:
    """Список портов-кандидатов.

    :param ports: явный список путей (режим ручного выбора) — фильтр по
        VID/PID при этом не применяется.
    """
    if ports is not None:
        return [_PortInfo(path=p) for p in prefer_serial_paths(ports)]

    from serial.tools import list_ports  # локальный импорт: тесты подменяют модуль

    found: List[_PortInfo] = []
    for info in list_ports.comports():
        if info.vid != vid or info.pid != pid:
            continue
        found.append(_PortInfo(
            path=info.device,
            vid=info.vid if info.vid is not None else vid,
            pid=info.pid if info.pid is not None else pid,
            serial_number=info.serial_number,
            product=info.product,
            manufacturer=info.manufacturer,
        ))
    keep = set(prefer_serial_paths([f.path for f in found]))
    return [f for f in found if f.path in keep]


def _open_with_retry(path: str, opener, deadline: float):
    """Открыть порт, повторяя попытку: Windows освобождает COM-порт с задержкой
    после завершения предыдущего процесса (типично для частых CLI-вызовов).

    :return: ``(transport, ошибка)``; при неудаче transport == None.
    """
    backoff = 0.15
    last_error = None
    while True:
        tr = None
        try:
            tr = opener(path)
            tr.open()
            return tr, None
        except Exception as exc:  # noqa: BLE001 - причины самые разные
            last_error = exc
            if tr is not None:
                try:
                    tr.close()
                except Exception:  # noqa: BLE001
                    pass
            if time.monotonic() + backoff >= deadline:
                return None, last_error
            time.sleep(backoff)
            backoff = min(backoff * 2, 0.6)


def probe_version(path: str, timeout_ms: float = 1500,
                  opener: Optional[Callable[[str], Transport]] = None
                  ) -> Optional[FirmwareVersion]:
    """Подтвердить, что на порте живёт VibeLamp, вернув версию прошивки.

    Два эффекта требуют повторов (проверено на живом устройстве, fw 1.0.32):

    1. открытие CDC-порта может перезапускать МК — первая команда попадает в
       загрузочное окно, поэтому признаком «живости» служит и баннер
       ``Welcome to CLI VibeLamp v...``;
    2. Windows освобождает порт с задержкой после закрытия предыдущим
       процессом — при частых вызовах CLI открытие надо повторить.

    :return: :class:`FirmwareVersion` либо ``None`` — порт не ответил
        (чужое устройство, занят, нет прав).
    """
    timeout = max(timeout_ms, 300) / 1000.0
    open_port = opener or _default_opener
    deadline = time.monotonic() + timeout
    tr, error = _open_with_retry(path, open_port, deadline)
    if tr is None:
        log.debug("probe: %s не открыт: %s", path, error)
        return None
    try:
        while time.monotonic() < deadline:
            tr.write_line("version")
            chunk = tr.read_until_any(_READY_MARKERS, 0.3)
            if not chunk:
                continue
            text = chunk.decode("latin-1", "replace")
            if _VERSION_MARKER.decode() in text:
                tail = tr.read_until(b"\n", 0.3).decode("latin-1", "replace")
                version = parse_version(text + tail)
            else:
                version = parse_version(text)      # версия из баннера загрузки
            if version is not None:
                return version
        return None
    except TransportError as exc:
        log.debug("probe: обмен с %s не удался: %s", path, exc)
        return None
    finally:
        tr.close()


def scan(timeout_ms: float = 1500, vid: int = DEFAULT_VID, pid: int = DEFAULT_PID,
         probe: bool = True, ports: Optional[Iterable[str]] = None,
         opener: Optional[Callable[[str], Transport]] = None,
         max_workers: int = 8) -> List[DeviceInfo]:
    """Найти все подключённые светильники.

    :param timeout_ms: бюджет на handshake ``version`` для одного порта.
    :param probe: подтверждать ли устройство ответом ``version``; при
        ``False`` возвращаются все порты, подходящие под VID/PID.
    :param ports: явный список путей вместо системного перечисления.
    :param opener: фабрика транспорта (инъекция для тестов/симулятора).
    :return: список :class:`DeviceInfo`, отсортированный по пути порта.
    """
    candidates = list_candidate_ports(vid=vid, pid=pid, ports=ports)
    if not candidates:
        return []

    def build(info: _PortInfo) -> Optional[DeviceInfo]:
        if not probe:
            return DeviceInfo(path=info.path, vid=info.vid, pid=info.pid,
                              serial_number=info.serial_number,
                              product=info.product, manufacturer=info.manufacturer)
        version = probe_version(info.path, timeout_ms=timeout_ms, opener=opener)
        if version is None:
            return None
        return DeviceInfo(path=info.path, vid=info.vid, pid=info.pid,
                          serial_number=info.serial_number, product=info.product,
                          manufacturer=info.manufacturer,
                          firmware_version=version, probed=True)

    workers = max(1, min(max_workers, len(candidates)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(build, candidates))
    return sorted([r for r in results if r is not None], key=lambda d: d.path)


def find_first(timeout_ms: float = 1500, **kwargs) -> DeviceInfo:
    """Как :func:`scan`, но бросает :class:`DeviceNotFoundError`, если пусто."""
    devices = scan(timeout_ms=timeout_ms, **kwargs)
    if not devices:
        raise DeviceNotFoundError()
    return devices[0]


def diagnose(vid: int = DEFAULT_VID, pid: int = DEFAULT_PID,
             timeout_ms: float = 600,
             opener: Optional[Callable[[str], Transport]] = None) -> List[dict]:
    """Объяснить, почему кандидаты не считаются VibeLamp.

    :return: список ``{"path", "status", "error"}``, где ``status`` — один из
        ``ok`` | ``no_reply`` | ``busy`` | ``open_failed``.
    """
    open_port = opener or _default_opener
    budget = max(0.1, timeout_ms / 1000.0)
    results: List[dict] = []
    for info in list_candidate_ports(vid=vid, pid=pid):
        try:
            tr = open_port(info.path)
            tr.open()
        except Exception as exc:  # noqa: BLE001
            status = "busy" if is_busy_error(exc) else "open_failed"
            results.append({"path": info.path, "status": status, "error": str(exc)})
            continue
        try:
            tr.write_line("version")
            chunk = tr.read_until_any(_READY_MARKERS, budget)
            results.append({"path": info.path,
                            "status": "ok" if chunk else "no_reply",
                            "error": None})
        except Exception as exc:  # noqa: BLE001
            results.append({"path": info.path, "status": "open_failed",
                            "error": str(exc)})
        finally:
            tr.close()
    return results


class LampWatcher:
    """Hot-plug подписка: опрос списка устройств в фоновом потоке.

    Опрос (polling) выбран намеренно: он не требует платформенных
    зависимостей (``pyudev`` / WMI / IOKit) и одинаково работает на трёх ОС.

    :param on_added: ``callable(DeviceInfo)`` — устройство появилось.
    :param on_removed: ``callable(DeviceInfo)`` — устройство исчезло.
    :param interval: период опроса, с.
    :param scan_kwargs: параметры, передаваемые :func:`scan`.
    :param scanner: функция поиска (инъекция для тестов).
    """

    def __init__(self,
                 on_added: Optional[Callable[[DeviceInfo], None]] = None,
                 on_removed: Optional[Callable[[DeviceInfo], None]] = None,
                 interval: float = 0.5,
                 scan_kwargs: Optional[dict] = None,
                 scanner: Optional[Callable[..., List[DeviceInfo]]] = None) -> None:
        self.on_added = on_added
        self.on_removed = on_removed
        self.interval = max(0.05, float(interval))
        self.scan_kwargs = dict(scan_kwargs or {})
        self.scanner = scanner or scan
        self._known: dict = {}
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> "LampWatcher":
        """Запустить фоновый опрос (идемпотентно)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self
            self._stop.clear()
            self._thread = threading.Thread(target=self._run,
                                            name="vibelamp-watcher", daemon=True)
            self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        """Остановить опрос и дождаться завершения потока."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None

    def __enter__(self) -> "LampWatcher":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # -------------------------------------------------------------------- state
    @property
    def devices(self) -> List[DeviceInfo]:
        """Снимок последних найденных устройств."""
        with self._lock:
            return list(self._known.values())

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --------------------------------------------------------------------- loop
    def poll_once(self) -> List[DeviceInfo]:
        """Один цикл опроса с генерацией событий (удобно в тестах)."""
        try:
            current = {d.key: d for d in self.scanner(**self.scan_kwargs)}
        except Exception as exc:  # noqa: BLE001 - watcher не должен падать
            log.debug("watcher: scan не удался: %s", exc)
            return self.devices
        with self._lock:
            added = [d for k, d in current.items() if k not in self._known]
            removed = [d for k, d in self._known.items() if k not in current]
            self._known = current
        for dev in added:
            self._notify(self.on_added, dev)
        for dev in removed:
            self._notify(self.on_removed, dev)
        return list(current.values())

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)

    @staticmethod
    def _notify(cb, dev: DeviceInfo) -> None:
        if cb is None:
            return
        try:
            cb(dev)
        except Exception as exc:  # noqa: BLE001 - ошибка колбэка не роняет watcher
            log.warning("watcher: колбэк завершился ошибкой: %s", exc)


def watch(on_added=None, on_removed=None, interval: float = 0.5,
          **scan_kwargs) -> LampWatcher:
    """Создать и запустить :class:`LampWatcher`.

    ::

        with vibelamp.watch(on_added=connect_to, interval=0.5) as w:
            ...   # подписка активна, пока блок не завершён
    """
    return LampWatcher(on_added=on_added, on_removed=on_removed,
                       interval=interval, scan_kwargs=scan_kwargs).start()
