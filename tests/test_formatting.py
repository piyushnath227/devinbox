"""Regression coverage for duration formatting (issue #1)."""

import pytest

from app.utils.formatting import format_duration


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-1, "0s"),
        (0, "0s"),
        (59, "59s"),
        (60, "1m"),
        (3599, "59m 59s"),
        (3600, "1h"),
        (3601, "1h 1s"),
        (3660, "1h 1m"),
        (3725, "1h 2m 5s"),
        (7325, "2h 2m 5s"),
        (90061, "25h 1m 1s"),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected
