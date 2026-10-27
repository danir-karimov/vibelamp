"""Общие pytest-фикстуры SDK VibeLamp."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:  # работа без установки пакета (editable)
    sys.path.insert(0, str(SRC))

from vibelamp.transport import LoopbackTransport  # noqa: E402


def echo_handler(tx: bytes) -> bytes:
    """Минимальная «прошивка»: отвечает ``OK`` на каждую строку-команду."""
    lines = [l for l in tx.decode("latin-1", "replace").split("\r\n") if l.strip()]
    if not lines:
        return b""
    return b"".join(b"OK\r\n" for _ in lines)


@pytest.fixture
def loopback() -> LoopbackTransport:
    """Открытый loopback-канал с эхо-обработчиком ``OK``."""
    tr = LoopbackTransport(name="test-loopback", handler=echo_handler)
    tr.open()
    yield tr
    tr.close()
