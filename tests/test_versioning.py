"""Тесты парсинга версий."""

from __future__ import annotations

from vibelamp.versioning import FirmwareVersion, parse_version


def test_parse_plain_version() -> None:
    assert parse_version("version: 1.0.25") == FirmwareVersion(1, 0, 25)


def test_parse_version_from_banner() -> None:
    text = "VibeLamp fw 1.2.100 (build 2026-10-27)\r\n"
    assert parse_version(text).as_tuple() == (1, 2, 100)


def test_parse_missing_version_returns_none() -> None:
    assert parse_version("OK") is None
    assert parse_version("") is None
    assert parse_version("no digits") is None


def test_versions_are_comparable() -> None:
    old = FirmwareVersion(1, 0, 25)
    new = FirmwareVersion(1, 1, 3)
    assert old < new
    assert new > old
    assert FirmwareVersion(1, 0, 25) == FirmwareVersion(1, 0, 25)
    assert sorted([new, old])[0] is old


def test_from_str_alias() -> None:
    assert FirmwareVersion.from_str("fw 2.3.4") == FirmwareVersion(2, 3, 4)
    assert FirmwareVersion.from_str("garbage") is None


def test_str_and_dict() -> None:
    v = FirmwareVersion(1, 0, 25)
    assert str(v) == "1.0.25"
    assert v.to_dict() == {"major": 1, "minor": 0, "patch": 25}
