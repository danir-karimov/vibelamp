"""Тесты моста Hermes -> VibeLamp (integrations/hermes).

Проверяем автомат состояний без железа и без USB: подменяем время и
отрисовку. Демон и плагин импортируются по пути, ставить пакет не нужно.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "integrations" / "hermes"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lampd = _load("vibelamp_hermes_lampd", INTEGRATION / "lampd.py")
speech = _load("vibelamp_hermes_speech", INTEGRATION / "speech_macos.py")


class FakePainter:
    """Заглушка отрисовки: запоминает показанные состояния."""

    def __init__(self) -> None:
        self.applied: list = []

    def apply(self, state: dict) -> bool:
        self.applied.append(state)
        return True

    @property
    def names(self) -> list:
        by_identity = {id(state): name for name, state in lampd.STATES.items()}
        return [by_identity.get(id(state), "?") for state in self.applied]

    def clear(self) -> None:
        self.applied.clear()


class Clock:
    """Ручные часы: тесты не спят."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBELAMP_LOG", str(tmp_path / "lampd.log"))
    painter = FakePainter()
    clock = Clock()
    return lampd.Engine(painter, clock=clock), painter, clock


def _command(state: dict, key: str) -> str:
    value = state.get(key)
    assert value is not None, key
    return value.to_command() if hasattr(value, "to_command") else str(value)


def test_palette_covers_every_documented_state() -> None:
    for name in ("arrive", "thinking", "tool", "question", "done",
                 "waiting", "idle", "error"):
        assert name in lampd.STATES, name
        assert 0 <= lampd.STATES[name]["bri"] <= 255


def test_palette_uses_the_sdk_effects() -> None:
    assert _command(lampd.STATES["thinking"], "effect") == "breathe 2000 255 36 0"
    assert _command(lampd.STATES["tool"], "effect") == "running 900 0 200 255"
    assert _command(lampd.STATES["question"], "effect") == "blink 900"
    assert "rgb" not in lampd.STATES["waiting"]        # дыхание не меняет цвет
    assert _command(lampd.STATES["waiting"], "effect") == "breathe 4000 0 153 0"
    assert _command(lampd.STATES["idle"], "effect") == "breathe 20000 0 153 0"
    assert lampd.STATES["done"]["rgb"] == (0, 204, 0)


def test_turn_start_flashes_white_then_falls_back_to_thinking(rig) -> None:
    engine, painter, clock = rig
    engine.feed({"event": "turn_start"})
    assert engine.current == "arrive"
    assert lampd.STATES["arrive"]["rgb"] == (255, 255, 255)

    clock.advance(lampd.FLASH_ARRIVE + 0.1)
    engine.tick()
    assert engine.current == "thinking"


def test_repeated_thinking_does_not_break_the_flash(rig) -> None:
    engine, painter, clock = rig
    engine.feed({"event": "turn_start"})
    engine.feed({"event": "thinking"})
    clock.advance(lampd.FLASH_ARRIVE / 2)
    engine.tick()
    assert engine.current == "arrive"


def test_quick_tool_does_not_strobe(rig) -> None:
    engine, painter, clock = rig
    engine.feed({"event": "turn_start"})
    clock.advance(lampd.FLASH_ARRIVE + 0.1)
    engine.tick()

    painter.clear()
    engine.feed({"event": "tool_start", "tool": "terminal"})
    clock.advance(0.4)
    engine.feed({"event": "tool_end", "tool": "terminal", "status": "ok"})
    engine.tick()

    assert engine.current == "thinking"
    assert "tool" not in painter.names


def test_long_tool_shows_the_cyan_wave(rig) -> None:
    engine, painter, clock = rig
    engine.feed({"event": "tool_start", "tool": "terminal"})
    clock.advance(lampd.TOOL_SHOW_AFTER + 0.1)
    engine.tick()

    assert engine.current == "tool"
    assert "tool" in painter.names


def test_heavy_tool_lights_up_immediately(rig) -> None:
    """Сеть и браузер не бывают мгновенными — ждать порог незачем."""
    engine, painter, clock = rig
    engine.feed({"event": "tool_start", "tool": "browser_exec"})

    assert engine.current == "tool"
    assert "tool" in painter.names

    engine.feed({"event": "tool_end", "tool": "browser_exec", "status": "ok"})
    engine.feed({"event": "thinking"})
    clock.advance(lampd.TOOL_HOLD_AFTER + 0.1)
    engine.tick()
    assert engine.current == "thinking"


def test_cyan_episode_outlives_the_tool_by_a_hold(rig) -> None:
    engine, painter, clock = rig
    engine.feed({"event": "tool_start", "tool": "terminal"})
    clock.advance(lampd.TOOL_SHOW_AFTER + 0.1)
    engine.tick()
    assert engine.current == "tool"

    engine.feed({"event": "tool_end", "tool": "terminal", "status": "ok"})
    engine.feed({"event": "thinking"})
    engine.tick()
    assert engine.current == "tool"          # ещё держим кадр

    clock.advance(lampd.TOOL_HOLD_AFTER + 0.1)
    engine.tick()
    assert engine.current == "thinking"


def test_failed_tool_raises_the_red_alarm(rig) -> None:
    engine, painter, clock = rig
    engine.feed({"event": "tool_start", "tool": "terminal"})
    engine.feed({"event": "tool_end", "tool": "terminal", "status": "error"})

    assert engine.current == "error"
    assert lampd.STATES["error"]["rgb"] == (255, 0, 0)
    assert _command(lampd.STATES["error"], "effect") == "blink 220"

    clock.advance(lampd.FLASH_ERROR + 0.1)
    engine.tick()
    assert engine.current == "thinking"


def test_clarify_asks_the_user_with_yellow_blink(rig) -> None:
    engine, _painter, _clock = rig
    engine.feed({"event": "tool_start", "tool": "clarify"})

    assert engine.current == "question"
    assert lampd.STATES["question"]["rgb"] == (255, 210, 40)
    assert _command(lampd.STATES["question"], "effect") == "blink 900"


def test_turn_end_goes_green_then_waits(rig) -> None:
    engine, _painter, clock = rig
    engine.feed({"event": "turn_start"})
    clock.advance(lampd.FLASH_ARRIVE + 0.1)
    engine.tick()

    engine.feed({"event": "turn_end"})
    assert engine.current == "done"
    assert lampd.STATES["done"]["rgb"] == (0, 204, 0)

    clock.advance(lampd.FLASH_DONE + 0.1)
    engine.tick()
    assert engine.current == "waiting"


def test_long_silence_switches_to_rare_breathing(rig) -> None:
    engine, _painter, clock = rig
    engine.feed({"event": "turn_end"})
    clock.advance(lampd.FLASH_DONE + 0.1)
    engine.tick()
    assert engine.current == "waiting"

    clock.advance(lampd.IDLE_AFTER + 1)
    engine.tick()
    assert engine.current == "idle"


# --------------------------------------------------------------------------- #
# источники событий
# --------------------------------------------------------------------------- #
def test_parse_agent_log_recognises_hermes_lines() -> None:
    turn = ("2026-11-08 14:39:05,677 INFO [s] agent.turn_context: "
            "conversation turn: session=s model=m")
    assert lampd.parse_agent_log(turn) == {"event": "turn_start"}

    end = ("2026-11-08 14:41:50,372 INFO [s] agent.conversation_loop: "
           "Turn ended: reason=text_response(finish_reason=stop)")
    assert lampd.parse_agent_log(end) == {"event": "turn_end"}

    tool = ("2026-11-08 14:39:10,489 INFO [s] agent.tool_executor: "
            "tool skill_view completed (0.02s, 16741 chars)")
    assert lampd.parse_agent_log(tool) == {"event": "tool_end", "status": "ok"}

    error = ("2026-11-08 14:39:14,497 WARNING [s] agent.tool_executor: "
             "Tool web_extract returned error (3.02s): {...}")
    assert lampd.parse_agent_log(error) == {"event": "error"}

    assert lampd.parse_agent_log("something unrelated") is None


def test_tail_returns_only_appended_lines(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"event": "old"}\n', encoding="utf-8")
    tail = lampd.Tail(path)

    path.write_text('{"event": "old"}\n{"event": "new"}\n', encoding="utf-8")
    assert tail.read() == ['{"event": "new"}']

    path.write_text('{"event": "after-truncate"}\n', encoding="utf-8")
    assert tail.read() == ['{"event": "after-truncate"}']


# --------------------------------------------------------------------------- #
# плагин
# --------------------------------------------------------------------------- #
def test_plugin_registers_all_hooks_and_writes_the_spool(tmp_path, monkeypatch) -> None:
    spool = tmp_path / "events.jsonl"
    monkeypatch.setenv("VIBELAMP_SPOOL", str(spool))
    plugin = _load("vibelamp_hermes_plugin",
                   INTEGRATION / "hermes_plugin" / "__init__.py")

    hooks: dict = {}

    class Ctx:
        def register_hook(self, name, callback):
            hooks[name] = callback

    plugin.register(Ctx())
    assert set(hooks) == {
        "pre_llm_call", "pre_api_request", "post_api_request",
        "api_request_error", "pre_tool_call", "post_tool_call", "post_llm_call",
    }

    hooks["pre_llm_call"](session_id="s1", platform="telegram")
    hooks["pre_tool_call"](session_id="s1", tool_name="terminal")
    hooks["post_tool_call"](session_id="s1", tool_name="terminal",
                            status="ok", duration_ms=12)
    hooks["post_llm_call"](session_id="s1", assistant_response="привет")

    events = [json.loads(line)
              for line in spool.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == [
        "turn_start", "tool_start", "tool_end", "turn_end",
    ]
    assert events[1]["tool"] == "terminal"
    assert events[2]["status"] == "ok"


def test_plugin_never_raises_into_the_agent(tmp_path) -> None:
    """Если spool недоступен, плагин молча теряет событие, но не падает."""
    plugin = _load("vibelamp_hermes_plugin_ro",
                   INTEGRATION / "hermes_plugin" / "__init__.py")
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    plugin.SPOOL = blocker / "events.jsonl"  # type: ignore[attr-defined]
    plugin.emit("turn_start")          # не должно бросить исключение


# --------------------------------------------------------------------------- #
# голос (только macOS)
# --------------------------------------------------------------------------- #
class FakeSpeaker:
    """Записывает вызовы вместо того, чтобы шуметь."""

    def __init__(self) -> None:
        self.said: list = []

    def say(self, text=None) -> bool:
        self.said.append(text)
        return True


class Recorder:
    """Подмена subprocess.Popen: запоминает argv."""

    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return object()


def test_speaker_is_silent_off_macos() -> None:
    recorder = Recorder()
    speaker = speech.Speaker(platform="linux", executable=None, runner=recorder)

    assert speaker.available is False
    assert speaker.say() is False
    assert recorder.calls == []


def test_speaker_runs_say_with_voice_and_phrase() -> None:
    recorder = Recorder()
    speaker = speech.Speaker(platform="darwin", executable="/usr/bin/say",
                             voice="Samantha", phrase="Job done.", runner=recorder)

    assert speaker.available is True
    assert speaker.say() is True
    assert recorder.calls == [["/usr/bin/say", "-v", "Samantha", "Job done."]]


def test_speaker_respects_the_off_switch() -> None:
    recorder = Recorder()
    speaker = speech.Speaker(platform="darwin", executable="/usr/bin/say",
                             enabled=False, runner=recorder)

    assert speaker.available is False
    assert speaker.say() is False
    assert recorder.calls == []


def test_engine_speaks_when_the_turn_finishes() -> None:
    painter, clock, speaker = FakePainter(), Clock(), FakeSpeaker()
    engine = lampd.Engine(painter, clock=clock, speaker=speaker)

    engine.feed({"event": "turn_start"})
    clock.advance(12)
    engine.feed({"event": "turn_end"})

    assert speaker.said == [None]      # фраза по умолчанию, из Speaker


def test_engine_keeps_quiet_for_short_turns(monkeypatch) -> None:
    monkeypatch.setattr(lampd, "SPEAK_AFTER", 20.0)
    painter, clock, speaker = FakePainter(), Clock(), FakeSpeaker()
    engine = lampd.Engine(painter, clock=clock, speaker=speaker)

    engine.feed({"event": "turn_start"})
    clock.advance(3)
    engine.feed({"event": "turn_end"})

    assert speaker.said == []


def test_engine_without_speaker_does_not_fail() -> None:
    painter, clock = FakePainter(), Clock()
    engine = lampd.Engine(painter, clock=clock, speaker=None)

    engine.feed({"event": "turn_start"})
    clock.advance(5)
    engine.feed({"event": "turn_end"})

    assert engine.current == "done"
