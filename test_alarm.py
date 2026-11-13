from datetime import datetime, timedelta

import pytest

from alarm import alarm_reasons, hour_choices, parse_until
from border import MINSK, Summary

NOW = datetime(2026, 11, 13, 22, 10, tzinfo=MINSK)


def summary(in_queue=10, estimate_min=30):
    return Summary(name="Брузги", now=NOW, in_queue=in_queue, priority_waiting=0,
                   sent_last_hour=10, sent_last_day=200, first_registered=None,
                   last_registered=None, last_called_at=None, recent_actual_wait=None,
                   estimate=None if estimate_min is None else timedelta(minutes=estimate_min),
                   estimate_low=None, estimate_high=None)


@pytest.mark.parametrize("text,expected", [
    ("04:00", datetime(2026, 11, 14, 4, 0, tzinfo=MINSK)),   # next morning
    ("4", datetime(2026, 11, 14, 4, 0, tzinfo=MINSK)),
    ("4.30", datetime(2026, 11, 14, 4, 30, tzinfo=MINSK)),
    ("23:45", datetime(2026, 11, 13, 23, 45, tzinfo=MINSK)),  # later today
    ("0400", datetime(2026, 11, 14, 4, 0, tzinfo=MINSK)),
])
def test_parse_until(text, expected):
    assert parse_until(text, NOW) == expected


@pytest.mark.parametrize("text", ["", "25:00", "abc", "12:61"])
def test_parse_until_invalid(text):
    assert parse_until(text, NOW) is None


def test_hour_choices_are_next_full_hours():
    hours = hour_choices(NOW, 4)
    assert hours == ["23:00", "00:00", "01:00", "02:00"]


def test_alarm_triggers_on_cars_or_wait():
    assert alarm_reasons(summary(in_queue=30, estimate_min=59)) == []
    assert alarm_reasons(summary(in_queue=31, estimate_min=10)) == ["31 cars in queue (> 30)"]
    assert alarm_reasons(summary(in_queue=5, estimate_min=60)) == ["wait ~1h 0m (≥ 1h 0m)"]
    assert len(alarm_reasons(summary(in_queue=50, estimate_min=120))) == 2


def test_no_estimate_only_cars_count():
    assert alarm_reasons(summary(in_queue=5, estimate_min=None)) == []
