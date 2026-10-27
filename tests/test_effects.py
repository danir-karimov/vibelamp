"""Тесты типизации эффектов и валидации параметров."""

from __future__ import annotations

import pytest

from vibelamp.effects import AgentState, Effect, EFFECT_SPECS, MODE_NAMES
from vibelamp.errors import ValidationError


def test_positional_and_keyword_forms_match() -> None:
    assert Effect.rainbow(40).to_command() == "rainbow 40"
    assert Effect.rainbow(speed_ms=40).to_command() == "rainbow 40"


def test_multi_arg_effect() -> None:
    assert Effect.wave(3500, 0, 140, 255).to_command() == "wave 3500 0 140 255"
    assert Effect.zen(4000, 0, 255, 120, 0, 180, 255).to_command() == \
        "zen 4000 0 255 120 0 180 255"


def test_no_arg_effect() -> None:
    assert Effect("on").to_command() == "on"
    assert Effect("off", ()).to_command() == "off"


def test_name_is_normalized() -> None:
    assert Effect("RGB", (1, 2, 3)).to_command() == "rgb 1 2 3"


@pytest.mark.parametrize("value", [-1, 256, 1000])
def test_rgb_range_validation(value) -> None:
    with pytest.raises(ValidationError) as exc:
        Effect.rgb(0, 0, value)
    assert "0..255" in str(exc.value)


def test_period_range_validation() -> None:
    with pytest.raises(ValidationError):
        Effect.blink(5)          # минимум 10 мс
    with pytest.raises(ValidationError):
        Effect.blink(60001)


def test_unknown_effect() -> None:
    with pytest.raises(ValidationError):
        Effect("unicorns", (1,))
    with pytest.raises(AttributeError):
        Effect.rainbows(1)


def test_wrong_argument_count() -> None:
    with pytest.raises(ValidationError):
        Effect("rgb", (1, 2))
    with pytest.raises(ValidationError):
        Effect.rainbow(1, 2)


def test_mixed_positional_and_keyword_rejected() -> None:
    with pytest.raises(ValidationError):
        Effect.rainbow(1, speed_ms=2)


def test_missing_keyword() -> None:
    with pytest.raises(ValidationError):
        Effect.wave(period_ms=100, r=0, g=0)


def test_non_integer_rejected() -> None:
    with pytest.raises(ValidationError):
        Effect.rgb("red", 0, 0)


def test_mode_must_use_set_mode() -> None:
    with pytest.raises(ValidationError):
        Effect("mode", ())


def test_no_arg_effect_rejects_args() -> None:
    with pytest.raises(ValidationError):
        Effect("off", (1,))


def test_parse_cli_line() -> None:
    assert Effect.parse("wave 3500 0 140 255") == Effect.wave(3500, 0, 140, 255)
    assert Effect.parse("  off  ") == Effect("off")
    with pytest.raises(ValidationError):
        Effect.parse("")
    with pytest.raises(ValidationError):
        Effect.parse("wave 1 2")


def test_every_spec_produces_valid_command() -> None:
    for name, spec in EFFECT_SPECS.items():
        effect = Effect(name, tuple(lo for _n, lo, _hi in spec))
        assert effect.to_command().startswith(name)


def test_all_names_lists_specs_and_no_args() -> None:
    names = list(Effect.all_names())
    assert "rainbow" in names and "off" in names


def test_agent_state_values() -> None:
    assert AgentState.WAITING.cli_argument == "waiting"
    for state in AgentState:
        assert state.value in MODE_NAMES
