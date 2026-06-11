from personal_telegram_bot.config import parse_user_ids
from personal_telegram_bot.bot import is_authorized


def test_parse_single_id():
    assert parse_user_ids("448383615") == frozenset({448383615})


def test_parse_multiple_ids_with_spaces():
    assert parse_user_ids("123, 456 ,789") == frozenset({123, 456, 789})


def test_parse_empty_string_is_empty():
    assert parse_user_ids("") == frozenset()


def test_authorized_user_accepted():
    assert is_authorized(448383615, frozenset({448383615}))


def test_unknown_user_rejected():
    assert not is_authorized(999, frozenset({448383615}))


def test_empty_allowlist_rejects_everyone():
    assert not is_authorized(448383615, frozenset())
