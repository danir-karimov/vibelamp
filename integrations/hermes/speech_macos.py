"""Озвучка «ход закончен» на macOS.

Голос — это удобно только на десктопе и только там, где есть `say`: в macOS.
Поэтому код вынесен в отдельный модуль, который на других платформах молчит
и ничего не ломает — интеграция ведёт себя одинаково везде, просто без звука.

На Linux аналог — `espeak`/`spd-say`, на Windows — SAPI; если понадобится,
добавляются рядом такой же маленькой реализацией.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Optional

__all__ = ["Speaker", "DEFAULT_PHRASE", "DEFAULT_VOICE"]

DEFAULT_VOICE = "Samantha"
DEFAULT_PHRASE = "Master, the job is done."


def _env_flag(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() not in ("0", "off", "false", "no")


class Speaker:
    """Говорит фразу через системный `say`. На не-macOS — тихо молчит.

    :param voice: имя голоса (`say -v '?'` покажет список).
    :param phrase: что произносить.
    :param enabled: принудительно включить/выключить; по умолчанию берётся
        из ``VIBELAMP_SPEECH`` (``0`` выключает).
    :param executable: путь к `say`; в тестах подменяется, чтобы не шуметь.
    :param platform: подмена ``sys.platform`` для тестов.
    :param runner: фабрика процесса (по умолчанию ``subprocess.Popen``).
    """

    def __init__(self, voice: Optional[str] = None, phrase: Optional[str] = None,
                 enabled: Optional[bool] = None,
                 executable: Optional[str] = None,
                 platform: Optional[str] = None,
                 runner=None) -> None:
        self.voice = voice or os.environ.get("VIBELAMP_VOICE") or DEFAULT_VOICE
        self.phrase = phrase or os.environ.get("VIBELAMP_PHRASE") or DEFAULT_PHRASE
        self.enabled = _env_flag("VIBELAMP_SPEECH") if enabled is None else bool(enabled)
        self.platform = platform if platform is not None else sys.platform
        if executable is not None:
            self.executable = executable
        elif self.platform == "darwin":
            self.executable = shutil.which("say")
        else:
            self.executable = None
        self._runner = runner or subprocess.Popen

    @property
    def available(self) -> bool:
        """Есть ли чем говорить."""
        return bool(self.enabled and self.executable)

    def say(self, text: Optional[str] = None) -> bool:
        """Произнести фразу, не блокируя вызывающий код. True — если запустили."""
        message = text or self.phrase
        executable = self.executable
        if not self.enabled or not executable or not message:
            return False
        try:
            self._runner([executable, "-v", self.voice, message],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         start_new_session=True)
            return True
        except OSError:
            # `say` пропал между проверкой и запуском — не повод падать.
            return False
