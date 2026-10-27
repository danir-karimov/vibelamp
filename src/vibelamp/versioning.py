"""Парсинг и сравнение версий прошивки VibeLamp (``MAJOR.MINOR.PATCH``).

Прошивка отдаёт версию в баннере загрузки, в ответе ``version``
(``version: 1.0.25``) и в многострочном ``status``. Здесь — единый парсер,
общий для ``discovery``, ``protocol`` и ``ota`` (сверка «нужно ли обновляться»).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

__all__ = ["FirmwareVersion", "parse_version"]

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True, order=True)
class FirmwareVersion:
    """Семантический номер прошивки; сравним через ``<``, ``>=`` и т. п."""

    major: int = 0
    minor: int = 0
    patch: int = 0

    @classmethod
    def from_str(cls, text: str) -> Optional["FirmwareVersion"]:
        """Разобрать первую встреченную тройку чисел, иначе ``None``."""
        return parse_version(text)

    def __str__(self) -> str:  # pragma: no cover - тривиально
        return f"{self.major}.{self.minor}.{self.patch}"

    def as_tuple(self) -> Tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def to_dict(self) -> dict:
        return {"major": self.major, "minor": self.minor, "patch": self.patch}


def parse_version(text: str) -> Optional[FirmwareVersion]:
    """Извлечь ``MAJOR.MINOR.PATCH`` из произвольного текста ответа.

    >>> parse_version("version: 1.0.25")
    FirmwareVersion(major=1, minor=0, patch=25)
    >>> parse_version("no digits here") is None
    True
    """
    if not text:
        return None
    m = _VERSION_RE.search(text)
    if not m:
        return None
    major, minor, patch = (int(g) for g in m.groups())
    return FirmwareVersion(major, minor, patch)
