"""Exit-code mapping. scanimage exits with the raw SANE status code."""

from __future__ import annotations

import pytest

from sheaf.status import Outcome, classify, sane_status


@pytest.mark.parametrize(
    ("code", "name"),
    [
        (0, "GOOD"),
        (2, "CANCELLED"),
        (3, "DEVICE_BUSY"),
        (5, "EOF"),
        (6, "JAMMED"),
        (7, "NO_DOCS"),
        (8, "COVER_OPEN"),
        (9, "IO_ERROR"),
        (11, "ACCESS_DENIED"),
    ],
)
def test_every_documented_code_is_mapped(code, name):
    assert sane_status(code) == name
    assert classify(code, 0).title


def test_success_codes():
    assert classify(0, 3).outcome is Outcome.SUCCESS
    assert classify(5, 3).outcome is Outcome.SUCCESS
    assert classify(0, 3).title == "Scanned 3 pages"
    assert classify(0, 1).title == "Scanned 1 page"


def test_empty_feeder_with_no_pages_is_an_error():
    result = classify(7, 0)
    assert result.is_error
    assert "feeder" in result.title.lower()


def test_end_of_stack_is_success_not_an_error():
    # Verified on the device: --batch exits 7 (NO_DOCS) when the feeder runs
    # out, which is exactly how a normal batch ends. Pages produced is what
    # distinguishes "finished" from "you forgot to load paper".
    result = classify(7, 4)
    assert result.outcome is Outcome.SUCCESS
    assert result.pages == 4
    assert not result.is_error


def test_cancellation_is_neither_success_nor_error():
    assert classify(2, 1).outcome is Outcome.CANCELLED
    # A killed child can report any code; the explicit flag wins.
    assert classify(9, 1, cancelled=True).outcome is Outcome.CANCELLED


def test_hard_errors_mention_pages_already_scanned():
    result = classify(6, 2)
    assert result.is_error
    assert "2 page(s)" in result.detail


def test_unknown_code_does_not_crash():
    result = classify(42, 0)
    assert result.is_error
    assert "42" in result.detail
    assert sane_status(42) == "UNKNOWN(42)"


def test_unknown_code_with_pages_is_treated_as_success():
    assert classify(42, 3).outcome is Outcome.SUCCESS
