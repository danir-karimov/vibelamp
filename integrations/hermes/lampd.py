"""Демон-мост VibeLamp для Hermes Agent.

Хуки Hermes (см. :mod:`hermes_plugin`) складывают по одной JSON-строке на
событие агента в spool-файл. Этот демон — единственный владелец устройства —
читает их и рисует состояние светом.

    arrive    белая вспышка      пришло сообщение пользователя
    thinking  алый пульс         модель работает
    tool      голубая волна      долгий инструмент занят
    question  жёлтое мигание     агент задаёт вопрос пользователю
    done      зелёная вспышка    ход завершён
    waiting   зелёное дыхание    ждёт следующего сообщения
    idle      редкое дыхание     тишина дольше IDLE_AFTER секунд
    error     красная сирена     инструмент или провайдер отвалились

Запуск вручную или через ``install.py`` (зарегистрирует сервисом)::

    python lampd.py --test       # прогнать палитру и выйти
    python lampd.py --verbose    # на переднем плане с логом в stdout
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:      # запуск как скрипта и импорт из тестов
    sys.path.insert(0, str(_HERE))

import vibelamp
from vibelamp import Effect

from speech_macos import Speaker  # noqa: E402  (после правки sys.path)

__all__ = ["Engine", "Painter", "STATES", "Tail", "parse_agent_log",
           "read_append", "spool_path", "log_path", "agent_log_path"]

#: Что показать для каждого состояния агента. ``bri`` применяется первым,
#: затем цвет, затем эффект (``blink`` мигает текущим цветом).
STATES = {
    "arrive": {"bri": 230, "rgb": (255, 255, 255)},
    "thinking": {"bri": 170, "effect": Effect.breathe(2000, 255, 36, 0)},
    "tool": {"bri": 160, "effect": Effect.running(900, 0, 200, 255)},
    "question": {"bri": 220, "rgb": (255, 210, 40), "effect": Effect.blink(900)},
    "done": {"bri": 255, "rgb": (0, 204, 0)},
    "waiting": {"bri": 255, "effect": Effect.breathe(4000, 0, 153, 0)},
    "idle": {"bri": 70, "effect": Effect.breathe(20000, 0, 153, 0)},
    "error": {"bri": 230, "rgb": (255, 0, 0), "effect": Effect.blink(220)},
    "boot": {"bri": 180, "rgb": (255, 30, 60), "effect": Effect.fadein(800)},
}

#: Показывать голубой только для инструментов длиннее этого порога, секунды.
#: Без задержки шустрый агент превращает лампу в стробоскоп:
#: голубой, алый, голубой, алый...
TOOL_SHOW_AFTER = 1.0
#: Инструменты, которые заведомо работают долго (сеть, браузер, делегирование).
#: Для них голубой включается сразу, не дожидаясь порога.
HEAVY_TOOLS = frozenset({
    "browser_exec", "computer_use", "delegate_task", "web_extract",
    "web_search", "image_generation", "vision_analyze",
})
#: Уходить из голубого состояния лениво, чтобы серия быстрых вызовов
#: читалась как один блок.
TOOL_HOLD_AFTER = 1.5
#: После стольких секунд тишины после хода — дышать редко.
IDLE_AFTER = float(os.environ.get("VIBELAMP_IDLE_AFTER", 180))
#: Длительности вспышек.
FLASH_ARRIVE = 0.35
FLASH_DONE = 3.0
FLASH_ERROR = 3.0
TICK = 0.1
#: Молчать, если ход был короче этого, секунды (0 — говорить всегда).
SPEAK_AFTER = float(os.environ.get("VIBELAMP_SPEAK_AFTER", 0))
#: Секунд между попытками переподключиться, если устройство пропало.
RECONNECT_EVERY = 2.0


def spool_path() -> Path:
    """Файл, куда плагин пишет события и откуда читает демон."""
    override = os.environ.get("VIBELAMP_SPOOL")
    if override:
        return Path(override)
    return Path.home() / ".vibelamp" / "events.jsonl"


def log_path() -> Path:
    override = os.environ.get("VIBELAMP_LOG")
    if override:
        return Path(override)
    return Path.home() / ".vibelamp" / "lampd.log"


def agent_log_path() -> Path:
    """Собственный лог Hermes — грубый запасной источник, пока плагин не загружен."""
    home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    return home / "logs" / "agent.log"


def log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
    if os.environ.get("VIBELAMP_VERBOSE"):
        print(line, flush=True)


# --------------------------------------------------------------------------- #
# Устройство
# --------------------------------------------------------------------------- #
class Painter:
    """Наносит состояние на лампу через SDK и переживает пропажу устройства.

    Один процесс — один владелец порта. Любая ошибка связи закрывает лампу;
    следующая попытка отрисовки переподключится заново (порт мог смениться).
    """

    def __init__(self, device: Optional[str] = None) -> None:
        self.device = device
        self.lamp: Optional["vibelamp.Lamp"] = None
        self.last_attempt = 0.0

    def _ensure_open(self) -> bool:
        if self.lamp is not None and self.lamp.is_open:
            return True
        now = time.time()
        if now - self.last_attempt < RECONNECT_EVERY:
            return False
        self.last_attempt = now
        try:
            lamp = vibelamp.Lamp(self.device)
            lamp.open()
            self.lamp = lamp
            log(f"connected {lamp.path}")
            return True
        except vibelamp.VibeLampError as exc:
            log(f"connect failed: {exc}")
            self.lamp = None
            return False

    def apply(self, state: dict) -> bool:
        if not self._ensure_open():
            return False
        lamp = self.lamp
        if lamp is None:
            return False
        try:
            lamp.set_brightness(int(state["bri"]))
            rgb = state.get("rgb")
            if rgb:
                lamp.set_rgb(*rgb)
            effect = state.get("effect")
            if effect is not None:
                lamp.effect(effect)
            return True
        except vibelamp.VibeLampError as exc:
            log(f"apply failed: {exc}")
            try:
                lamp.close()
            except Exception:
                pass
            self.lamp = None
            return False


# --------------------------------------------------------------------------- #
# Автомат состояний
# --------------------------------------------------------------------------- #
class Engine:
    """Событие -> свет. Чистая логика, единственный выход — ``painter.apply``.

    :param painter: объект с методом ``apply(state) -> bool`` (в тестах —
        записывающая заглушка, в работе — :class:`Painter`).
    :param clock: подменяемое время, чтобы тесты не спали.
    :param speaker: озвучка «ход закончен» (:class:`speech_macos.Speaker`)
        или ``None``, если говорить нечем.
    """

    def __init__(self, painter, clock: Callable[[], float] = time.time,
                 speaker=None) -> None:
        self.painter = painter
        self.clock = clock
        self.speaker = speaker
        self.current = ""
        self.last_sent = 0.0
        self.turn_active = False
        self.turn_started_at = 0.0
        self.turn_duration = 0.0
        self.transient_until = 0.0
        self.after_transient = ""
        self.pending_tool: Optional[str] = None
        self.pending_at = 0.0
        self.revert_at = 0.0
        self.waiting_since = 0.0
        self.events_seen = 0

    # -- отрисовка ---------------------------------------------------------
    def render(self, name: str, force: bool = False) -> bool:
        state = STATES.get(name)
        if state is None:
            return False
        now = self.clock()
        if not force and name == self.current and now - self.last_sent < 30:
            return True
        if not self.painter.apply(state):
            log(f"state {name}: lamp not reachable")
            return False
        if name != self.current:
            log(f"state -> {name}")
        self.current = name
        self.last_sent = now
        if name == "done" and self.speaker is not None and \
                self.turn_duration >= SPEAK_AFTER:
            self.speaker.say()
        return True

    def flash(self, name: str, seconds: float, after: str) -> None:
        now = self.clock()
        self.transient_until = now + seconds
        self.after_transient = after
        self.render(name)

    # -- события -----------------------------------------------------------
    def feed(self, event: dict) -> None:
        kind = event.get("event")
        now = self.clock()
        self.events_seen += 1
        if not kind:
            return

        if kind == "turn_start":
            self.turn_active = True
            self.turn_started_at = now
            self.waiting_since = 0.0
            self.flash("arrive", FLASH_ARRIVE, "thinking")

        elif kind == "thinking":
            self.turn_active = True
            if self.current == "tool":
                return              # даём голубому эпизоду доиграть такт
            if self.transient_until and now < self.transient_until:
                return              # идущую вспышку не сбиваем
            if self.current != "error":
                self.render("thinking")

        elif kind == "tool_start":
            self.transient_until = 0.0
            self.revert_at = 0.0
            if event.get("tool") == "clarify":
                # Агент заблокирован на вопросе к пользователю.
                self.render("question")
            else:
                tool = str(event.get("tool") or "")
                if tool in HEAVY_TOOLS:
                    # Сеть и браузер заведомо не мгновенные — светим сразу.
                    self.pending_tool = None
                    self.render("tool")
                else:
                    self.pending_tool = tool
                    self.pending_at = now + TOOL_SHOW_AFTER

        elif kind == "tool_end":
            self.pending_tool = None
            status = str(event.get("status") or "").lower()
            if status in ("error", "failed"):
                self.flash("error", FLASH_ERROR, "thinking")
            elif self.current == "question":
                pass                # мигаем жёлтым до конца хода
            elif self.current == "tool":
                self.revert_at = now + TOOL_HOLD_AFTER

        elif kind == "error":
            self.flash("error", FLASH_ERROR, "thinking")

        elif kind == "turn_end":
            if not self.turn_active:
                return          # повтор: ход уже закрыт, второй раз не празднуем
            self.turn_active = False
            self.waiting_since = now
            self.turn_duration = (now - self.turn_started_at
                                  if self.turn_started_at else 0.0)
            self.flash("done", FLASH_DONE, "waiting")

    # -- периодические проверки -------------------------------------------
    def tick(self) -> None:
        now = self.clock()
        if self.pending_tool and now >= self.pending_at:
            self.pending_tool = None
            self.render("tool")
        if self.revert_at and now >= self.revert_at:
            self.revert_at = 0.0
            self.render("thinking" if self.turn_active else "waiting")
        if self.transient_until and now >= self.transient_until:
            target, self.after_transient = self.after_transient, ""
            self.transient_until = 0.0
            self.render(target)
        elif self.current == "waiting" and self.waiting_since and \
                now - self.waiting_since > IDLE_AFTER:
            self.render("idle")


# --------------------------------------------------------------------------- #
# Чтение источников событий
# --------------------------------------------------------------------------- #
def read_append(path: Path, offset: int):
    """Вернуть ``(новое_смещение, строки, inode)`` для дописанного после offset."""
    try:
        stat = path.stat()
    except OSError:
        return offset, [], None
    if stat.st_size < offset:          # файл усечён или подменён
        offset = 0
    if stat.st_size == offset:
        return offset, [], stat.st_ino
    with open(path, "rb") as handle:
        handle.seek(offset)
        data = handle.read(1 << 20)
    return offset + len(data), data.decode("utf-8", "replace").splitlines(), stat.st_ino


def parse_agent_log(line: str) -> Optional[dict]:
    """Грубый запасной вариант: превратить строку лога Hermes в событие.

    Работает только пока плагин не загружен (он активируется после рестарта
    шлюза). Теряет имена инструментов и их старт — отсюда и «грубый».
    """
    if "agent.turn_context: conversation turn:" in line:
        return {"event": "turn_start"}
    if "Turn ended:" in line:
        return {"event": "turn_end"}
    # Hermes пишет и «tool x completed», и «Tool x returned error» — регистр
    # в этих двух местах разный, поэтому сравниваем в нижнем регистре.
    lowered = line.lower()
    if "agent.tool_executor:" in lowered:
        if " completed (" in lowered:
            return {"event": "tool_end", "status": "ok"}
        if " returned error" in lowered:
            return {"event": "error"}
    if "API call #" in line:
        return {"event": "thinking"}
    return None


class Tail:
    """Следит за одним файлом, переживая усечение и ротацию."""

    def __init__(self, path: Path, from_end: bool = True) -> None:
        self.path = path
        self.offset = 0
        self.inode: Optional[int] = None
        try:
            stat = path.stat()
            self.offset = stat.st_size if from_end else 0
            self.inode = stat.st_ino
        except OSError:
            pass

    def read(self) -> Iterable[str]:
        offset, lines, inode = read_append(self.path, self.offset)
        if inode is not None and self.inode is not None and inode != self.inode:
            offset, lines, inode = read_append(self.path, 0)
        self.offset = offset
        if inode is not None:
            self.inode = inode
        return lines


def events_from_spool(tail: Tail) -> Iterable[dict]:
    for line in tail.read():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def events_from_agent_log(tail: Tail) -> Iterable[dict]:
    for line in tail.read():
        event = parse_agent_log(line)
        if event:
            yield event


#: Сколько секунд тишины в спуле считаются «плагин не работает».
FALLBACK_AFTER = 20.0


def pump(engine: "Engine", spool: Tail, agent_log: Tail,
         last_plugin_event: float, now: Optional[float] = None) -> float:
    """Один проход по источникам событий. Возвращает время последнего события.

    Спóл читается всегда. Запасной лог агента тоже **читается** всегда, но
    его события применяются, только пока плагин молчит. Иначе получается
    классическая засада: за время хода в логе копится история, и в момент,
    когда плагин замолкает, она проигрывается заново — вторая зелёная вспышка
    и второй голос через двадцать секунд после финала.
    """
    moment = time.time() if now is None else now
    for event in events_from_spool(spool):
        last_plugin_event = moment
        engine.feed(event)

    lines = list(agent_log.read())          # курсор двигаем в любом случае
    if moment - last_plugin_event > FALLBACK_AFTER:
        for line in lines:
            event = parse_agent_log(line)
            if event:
                engine.feed(event)
    return last_plugin_event


# --------------------------------------------------------------------------- #
# Точка входа
# --------------------------------------------------------------------------- #
def run(device: Optional[str] = None) -> None:
    painter = Painter(device)
    speaker = Speaker()
    engine = Engine(painter, speaker=speaker)
    # Дежурный зелёный, а не «boot»: boot — тёмно-розовый (255, 30, 60),
    # и он висит до таймаута простоя. После перезапуска демона лампа
    # выглядела сломанной.
    engine.render("waiting")

    spool = Tail(spool_path())
    agent_log = Tail(agent_log_path())
    last_plugin_event = 0.0

    def shutdown(*_args) -> None:
        painter.apply({"bri": 60, "rgb": (20, 0, 10)})
        if painter.lamp is not None:
            painter.lamp.close()
        log("daemon stopped")
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    log(f"daemon started (spool={spool_path()})")

    while True:
        last_plugin_event = pump(engine, spool, agent_log, last_plugin_event)
        engine.tick()
        time.sleep(TICK)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="VibeLamp bridge for Hermes Agent")
    parser.add_argument("--test", action="store_true",
                        help="прогнать палитру один раз и выйти")
    parser.add_argument("--port", help="порт; по умолчанию автопоиск")
    parser.add_argument("--verbose", action="store_true", help="лог в stdout")
    args = parser.parse_args(argv)

    if args.verbose:
        os.environ["VIBELAMP_VERBOSE"] = "1"

    if args.test:
        painter = Painter(args.port)
        for name in ("boot", "thinking", "tool", "question",
                     "done", "waiting", "idle"):
            painter.apply(STATES[name])
            time.sleep(1.5)
        if painter.lamp is not None:
            painter.lamp.close()
        print("palette painted")
        return 0

    run(device=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
