from datetime import datetime

from personal_telegram_bot.cli import FAILURE_ALERT_WINDOW_HOURS, failure_window_key


def test_same_window_same_key():
    a = failure_window_key("dotfiles-sync.service", datetime(2026, 6, 11, 13, 0))
    b = failure_window_key("dotfiles-sync.service", datetime(2026, 6, 11, 13, 40))
    assert a == b


def test_next_window_differs():
    a = failure_window_key("dotfiles-sync.service", datetime(2026, 6, 11, 13, 0))
    b = failure_window_key(
        "dotfiles-sync.service",
        datetime(2026, 6, 11, 13 + FAILURE_ALERT_WINDOW_HOURS, 0),
    )
    assert a != b


def test_key_scoped_per_unit():
    now = datetime(2026, 6, 11, 13, 0)
    assert failure_window_key("a.service", now) != failure_window_key("b.service", now)


def test_day_boundary_differs():
    a = failure_window_key("a.service", datetime(2026, 6, 11, 1, 0))
    b = failure_window_key("a.service", datetime(2026, 6, 12, 1, 0))
    assert a != b
