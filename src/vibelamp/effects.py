"""Типизированные эффекты и состояния VibeLamp поверх ASCII CLI.

Спецификация параметров взята из `docs/protocol.md` и сверена с парсером CLI
прошивки VibeLamp. Валидация диапазонов выполняется **до**
отправки команды, чтобы не слать заведомо неверные данные в прошивку.

::

    Effect.rainbow(40)                          # "rainbow 40"
    Effect.rainbow(speed_ms=40)                 # то же самое
    Effect.wave(period_ms=3500, r=0, g=140, b=255)
    Effect("rainbow", (40,))                    # явная форма
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List, Sequence, Tuple

from .errors import ValidationError

__all__ = ["Effect", "EFFECT_SPECS", "EFFECT_NO_ARGS", "AgentState",
           "MODE_NAMES", "MAX_BRIGHTNESS"]

#: Верхняя граница яркости (8-битный канал прошивки).
MAX_BRIGHTNESS = 255

#: Спецификация аргументов: имя эффекта -> кортеж ``(имя_параметра, min, max)``.
EFFECT_SPECS: Dict[str, Tuple[Tuple[str, int, int], ...]] = {
    "rgb": (("r", 0, 255), ("g", 0, 255), ("b", 0, 255)),
    "bri": (("value", 0, 255),),
    "blink": (("period_ms", 10, 60000),),
    "fadein": (("time_ms", 10, 60000),),
    "fadeout": (("time_ms", 10, 60000),),
    "rainbow": (("speed_ms", 1, 10000),),
    "fire": (("cool", 0, 255), ("spark", 0, 255)),
    "running": (("speed_ms", 1, 10000), ("r", 0, 255), ("g", 0, 255), ("b", 0, 255)),
    "sparkle": (("speed_ms", 1, 10000), ("max", 1, 24)),
    "breathe": (("period_ms", 100, 60000), ("r", 0, 255), ("g", 0, 255), ("b", 0, 255)),
    "wave": (("period_ms", 100, 60000), ("r", 0, 255), ("g", 0, 255), ("b", 0, 255)),
    "aurora": (("speed_ms", 100, 60000),),
    "nebula": (("speed_ms", 100, 60000), ("r", 0, 255), ("g", 0, 255), ("b", 0, 255)),
    "orbit": (("speed_ms", 100, 60000), ("r", 0, 255), ("g", 0, 255), ("b", 0, 255)),
    "candle": (("speed_ms", 1000, 10000), ("smooth", 1, 255)),
    "ripple": (("period_ms", 1000, 20000), ("r", 0, 255), ("g", 0, 255), ("b", 0, 255)),
    "sunset": (("period_ms", 2000, 60000), ("sat", 0, 255)),
    "zen": (("period_ms", 2000, 30000),
            ("r1", 0, 255), ("g1", 0, 255), ("b1", 0, 255),
            ("r2", 0, 255), ("g2", 0, 255), ("b2", 0, 255)),
    "breeze": (("speed_ms", 2000, 20000), ("r", 0, 255), ("g", 0, 255), ("b", 0, 255)),
}

#: Безаргументные команды.
EFFECT_NO_ARGS: Tuple[str, ...] = ("on", "off", "stop")

#: Высокоуровневые состояния ИИ-агента -> аргумент CLI-команды ``mode``.
MODE_NAMES: Dict[str, str] = {
    "reasoning": "reasoning",
    "act": "act",
    "waiting": "waiting",
    "completed": "completed",
    "error": "error",
    "idle": "idle",
}


class AgentState(str, Enum):
    """Состояние ИИ-агента, транслируемое в пресет эффекта прошивки."""

    REASONING = "reasoning"
    ACT = "act"
    WAITING = "waiting"
    COMPLETED = "completed"
    ERROR = "error"
    IDLE = "idle"

    @property
    def cli_argument(self) -> str:
        return self.value


def _coerce(name: str, spec: Sequence[Tuple[str, int, int]],
            args: Sequence, kwargs: Dict) -> Tuple[int, ...]:
    """Привести args/kwargs к кортежу int и проверить диапазоны."""
    if kwargs and args:
        raise ValidationError(
            f"'{name}': передавайте либо позиционные аргументы, либо именованные",
            "пример: Effect.rainbow(speed_ms=40)")
    if kwargs:
        values = []
        for arg_name, _lo, _hi in spec:
            if arg_name not in kwargs:
                raise ValidationError(
                    f"'{name}': не задан параметр '{arg_name}'",
                    f"ожидается {len(spec)}: {[s[0] for s in spec]}")
            values.append(kwargs[arg_name])
    else:
        if len(args) != len(spec):
            raise ValidationError(
                f"'{name}': ожидалось {len(spec)} параметра, получено {len(args)}",
                f"параметры: {[s[0] for s in spec]}")
        values = list(args)

    out: List[int] = []
    for (arg_name, lo, hi), raw in zip(spec, values):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValidationError(
                f"'{name}.{arg_name}': ожидалось целое число, получено {raw!r}") from None
        if not lo <= value <= hi:
            raise ValidationError(
                f"'{name}.{arg_name}' = {value} вне диапазона {lo}..{hi}")
        out.append(value)
    return tuple(out)


class _EffectMeta(type):
    """Метакласс: ``Effect.rainbow(...)`` создаёт эффект по имени из спецификации."""

    def __getattr__(cls, name: str):
        if name in EFFECT_SPECS:
            spec = EFFECT_SPECS[name]

            def build(*args, **kwargs) -> "Effect":
                return Effect(name, _coerce(name, spec, args, kwargs))

            build.__name__ = name
            build.__qualname__ = f"Effect.{name}"
            build.__doc__ = ("Эффект ``%s``: " % name +
                             ", ".join(f"{n} ({lo}..{hi})" for n, lo, hi in spec))
            return build
        raise AttributeError(f"{cls.__name__} не знает эффект '{name}'")


@dataclass(frozen=True)
class Effect(metaclass=_EffectMeta):
    """Эффект светильника: имя + уже провалидированные параметры."""

    name: str
    args: Tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise ValidationError(f"некорректное имя эффекта: {self.name!r}")
        name = self.name.strip().lower()
        object.__setattr__(self, "name", name)
        if name in EFFECT_NO_ARGS:
            if self.args:
                raise ValidationError(f"'{name}' не принимает параметров")
            object.__setattr__(self, "args", ())
            return
        if name == "mode":
            raise ValidationError(
                "'mode' задаётся через Lamp.set_mode(AgentState...)",
                "см. docs/protocol.md")
        spec = EFFECT_SPECS.get(name)
        if spec is None:
            raise ValidationError(
                f"неизвестный эффект '{name}'",
                f"доступно: {', '.join(sorted(EFFECT_SPECS))}")
        object.__setattr__(self, "args",
                           _coerce(name, spec, tuple(self.args), {}))

    def to_command(self) -> str:
        """CLI-строка для отправки в прошивку."""
        if not self.args:
            return self.name
        return f"{self.name} " + " ".join(str(a) for a in self.args)

    def __str__(self) -> str:  # pragma: no cover - тривиально
        return self.to_command()

    @staticmethod
    def parse(line: str) -> "Effect":
        """Разобрать CLI-строку (``"wave 3500 0 140 255"``) в эффект."""
        parts = (line or "").strip().split()
        if not parts:
            raise ValidationError("пустая команда эффекта")
        name = parts[0].lower()
        raw_args = parts[1:]
        if name in EFFECT_NO_ARGS:
            return Effect(name)
        spec = EFFECT_SPECS.get(name)
        if spec is None:
            raise ValidationError(f"неизвестный эффект '{name}'")
        if len(raw_args) != len(spec):
            raise ValidationError(
                f"'{name}': ожидалось {len(spec)} параметра, получено {len(raw_args)}")
        return Effect(name, _coerce(name, spec, raw_args, {}))

    @staticmethod
    def all_names() -> Iterable[str]:
        """Все известные имена эффектов (включая безаргументные)."""
        return tuple(sorted(EFFECT_SPECS)) + EFFECT_NO_ARGS
