"""Family display names (contract.FAMILY_LABELS) and the read-time stamp.

The label is DISPLAY ONLY - the stored `family` value must stay family1|2|3
(renaming a persisted column orphans rows under a ReplacingMergeTree), and the
label must stay a category noun so it reads correctly next to a `satisfied`
status on /eval/conformance and the per-session verdict reads.
"""

from datetime import datetime

from evalengine import ch
from evalengine.contract import FAMILY_LABELS, family_label


def test_every_family_has_a_label():
    assert FAMILY_LABELS == {
        "family1": "Policy",
        "family2": "Integrity",
        "family3": "Conformance",
    }


def test_unknown_family_passes_through_rather_than_blanking():
    assert family_label("family9") == "family9"
    assert family_label("") == ""


def test_labels_are_category_nouns_not_outcome_words():
    # The same label rides on satisfied verdicts and conformance tags, so an
    # outcome word ("Violations", "Failures") would contradict the status.
    for label in FAMILY_LABELS.values():
        low = label.lower()
        assert "violation" not in low and "failure" not in low and "error" not in low


class _FakeResult:
    def __init__(self, column_names, result_rows):
        self.column_names = column_names
        self.result_rows = result_rows


class _FakeClient:
    def __init__(self, result):
        self._result = result

    def query(self, sql, parameters=None):
        return self._result


def _with_client(monkeypatch, column_names, result_rows):
    monkeypatch.setattr(ch, "client", lambda: _FakeClient(_FakeResult(column_names, result_rows)))


def test_rows_stamps_family_label_on_every_read(monkeypatch):
    _with_client(monkeypatch, ["session_id", "family", "check_id"],
                 [("s1", "family1", "field_restriction"), ("s2", "family2", "param_taint")])
    out = ch.rows("SELECT 1")
    assert [r["family_label"] for r in out] == ["Policy", "Integrity"]
    # the raw stored value is untouched - it is what filters/dedup key on
    assert [r["family"] for r in out] == ["family1", "family2"]


def test_rows_without_a_family_column_is_unchanged(monkeypatch):
    _with_client(monkeypatch, ["session_id", "spans"], [("s1", 3)])
    out = ch.rows("SELECT 1")
    assert out == [{"session_id": "s1", "spans": 3}]


def test_rows_still_coerces_datetimes(monkeypatch):
    _with_client(monkeypatch, ["family", "evaluated_at"],
                 [("family3", datetime(2026, 1, 2, 3, 4, 5))])
    out = ch.rows("SELECT 1")
    assert out[0]["family_label"] == "Conformance"
    assert out[0]["evaluated_at"].startswith("2026-01-02T03:04:05")
