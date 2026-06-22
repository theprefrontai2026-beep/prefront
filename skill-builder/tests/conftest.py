"""Shared test fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the package importable when running `pytest` from the repo root or here.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillbuilder.store import Store  # noqa: E402


@pytest.fixture
def store(tmp_path) -> Store:
    """A fresh Store backed by a temp SQLite file."""
    return Store(str(tmp_path / "test.db"))


class _Sec:
    def __init__(self, sid="sec1", path="4.1"):
        self.section_id = sid
        self.section_path = path
        self.heading = "h"
        self.page_start = 1
        self.page_end = 1
        self.markdown = "section body"


class _Clause:
    def __init__(self, cid="clause_0001", ctype="restriction", disposition=None):
        self.clause_id = cid
        self.section_id = "sec1"
        self.section_path = "4.1"
        self.page_number = 1
        self.paragraph_ref = "p1"
        self.clause_type = ctype
        self.disposition = disposition
        self.source_text = "No order accepted for accounts on hold"


@pytest.fixture
def section():
    return _Sec


@pytest.fixture
def clause():
    return _Clause
