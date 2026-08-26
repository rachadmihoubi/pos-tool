"""
Tests for poslib/updater.py - the silent auto-update check.

Entirely mocked - never makes a real network call, never spawns a real
process. See docs/superpowers/specs/2026-08-26-component3-auto-update-design.md
for the design this implements.
"""

from __future__ import annotations

from pathlib import Path

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
