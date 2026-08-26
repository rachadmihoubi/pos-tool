"""
Tests for poslib/updater.py - the silent auto-update check.

Entirely mocked - never makes a real network call, never spawns a real
process. See docs/superpowers/specs/2026-08-26-component3-auto-update-design.md
for the design this implements.
"""

from __future__ import annotations

import pytest

from poslib import updater


def test_current_version_reads_and_parses_the_version_file(monkeypatch, tmp_path):
    version_file = tmp_path / "VERSION"
    version_file.write_text("2.5.11\n", encoding="utf-8")
    monkeypatch.setattr(updater, "app_root", lambda: tmp_path)

    assert updater.current_version() == (2, 5, 11)


def test_current_version_falls_back_to_zero_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "app_root", lambda: tmp_path)

    assert updater.current_version() == (0, 0, 0)


@pytest.mark.parametrize("text,expected", [
    ("1.2.3", (1, 2, 3)),
    ("v1.2.3", (1, 2, 3)),
    ("V1.2.3", (1, 2, 3)),
    (" 1.2.3 \n", (1, 2, 3)),
    ("1.2", None),
    ("1.2.3.4", None),
    ("not-a-version", None),
    ("", None),
])
def test_parse_version(text, expected):
    assert updater._parse_version(text) == expected


def test_current_version_catches_read_failures(monkeypatch, tmp_path):
    # Create VERSION as a directory instead of a file to trigger IsADirectoryError
    # when read_text() is called (IsADirectoryError is a subclass of OSError)
    version_dir = tmp_path / "VERSION"
    version_dir.mkdir()
    monkeypatch.setattr(updater, "app_root", lambda: tmp_path)

    # Attempting to read a directory as text will raise OSError
    assert updater.current_version() == (0, 0, 0)


def test_current_version_catches_decode_failures(monkeypatch, tmp_path):
    version_file = tmp_path / "VERSION"
    version_file.write_bytes(b"\xff\xfe")  # Invalid UTF-8
    monkeypatch.setattr(updater, "app_root", lambda: tmp_path)

    # This should catch UnicodeDecodeError (a ValueError) and return (0, 0, 0)
    assert updater.current_version() == (0, 0, 0)
