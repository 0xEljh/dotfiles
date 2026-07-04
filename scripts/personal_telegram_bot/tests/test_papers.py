"""Weekly papers dispatch: nudges refinement of unrefined Paper Inbox sightings
(Status select ≠ "landed"). Deduped once per ISO week via the StateDB; the
integration is optional, so missing Notion config is a quiet no-op."""

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import personal_telegram_bot.digests as digests
from personal_telegram_bot.config import Config
from personal_telegram_bot.formatters import format_papers
from personal_telegram_bot.providers import paper_inbox

TZ = ZoneInfo("Asia/Singapore")
NOW = datetime(2026, 7, 4, 9, 0, tzinfo=TZ)  # ISO week 2026-W27
NEXT_WEEK = NOW + timedelta(days=7)  # ISO week 2026-W28


def _cfg(tmp_path):
    return Config(
        telegram_token="tok",
        default_chat_id=1,
        allowed_user_ids=frozenset(),
        notion_token="ntn",
        bread_datasource_id="ds",
        tz=TZ,
        db_path=tmp_path / "state.sqlite3",
        health_units=[],
        health_urls=[],
        aw_data_dir=tmp_path,
        aw_max_age_hours=26.0,
        aw_systematic_after_hours=24.0,
        aw_stale_reminder_hours=12,
        life_db_path=tmp_path / "life.sqlite3",
        life_ingest_token=None,
        life_ingest_bind="127.0.0.1",
        life_ingest_port=8830,
        paper_inbox_datasource_id="papers-ds",
        paper_inbox_url="https://notion.so/paper-inbox",
    )


def _capture_sends(monkeypatch):
    sent = []
    monkeypatch.setattr(
        digests,
        "send_message",
        lambda token, chat, text, parse_mode=None: sent.append(text) or 99,
    )
    return sent


def _stub_pending(monkeypatch, titles):
    monkeypatch.setattr(paper_inbox, "fetch_pending", lambda *a, **k: list(titles))


def _fail_fetch(*a, **k):
    raise AssertionError("fetch_pending must not be called when unconfigured")


def test_sends_once_then_dedupes_within_iso_week(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    sent = _capture_sends(monkeypatch)
    _stub_pending(monkeypatch, ["Attention Is All You Need", "Mamba"])

    first = digests.deliver_papers_digest(cfg, trigger="timer", now=NOW)
    second = digests.deliver_papers_digest(cfg, trigger="timer", now=NOW)
    next_week = digests.deliver_papers_digest(cfg, trigger="timer", now=NEXT_WEEK)

    assert first is True
    assert second is False
    assert next_week is True  # new ISO week → new dispatch
    assert len(sent) == 2
    assert "week 27" in sent[0]
    assert "2 sightings awaiting refinement" in sent[0]
    assert "Attention Is All You Need" in sent[0]


def test_dry_run_prints_but_does_not_send_or_record(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    sent = _capture_sends(monkeypatch)
    _stub_pending(monkeypatch, ["Mamba"])

    assert digests.deliver_papers_digest(cfg, trigger="timer", dry_run=True, now=NOW) is True
    assert sent == []
    assert "Mamba" in capsys.readouterr().out
    # Not recorded, so a real send still goes through afterwards.
    assert digests.deliver_papers_digest(cfg, trigger="timer", now=NOW) is True
    assert len(sent) == 1


def test_missing_datasource_returns_false_without_sending(tmp_path, monkeypatch, capsys):
    cfg = replace(_cfg(tmp_path), paper_inbox_datasource_id=None)
    sent = _capture_sends(monkeypatch)
    monkeypatch.setattr(paper_inbox, "fetch_pending", _fail_fetch)

    assert digests.deliver_papers_digest(cfg, trigger="timer", now=NOW) is False
    assert sent == []
    assert "not configured" in capsys.readouterr().out


def test_missing_notion_token_returns_false_without_sending(tmp_path, monkeypatch):
    cfg = replace(_cfg(tmp_path), notion_token=None)
    sent = _capture_sends(monkeypatch)
    monkeypatch.setattr(paper_inbox, "fetch_pending", _fail_fetch)

    assert digests.deliver_papers_digest(cfg, trigger="timer", now=NOW) is False
    assert sent == []


def test_zero_pending_sends_clear_one_liner(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    sent = _capture_sends(monkeypatch)
    _stub_pending(monkeypatch, [])

    assert digests.deliver_papers_digest(cfg, trigger="timer", now=NOW) is True
    assert len(sent) == 1
    assert "Paper inbox clear" in sent[0]


# --- format_papers: terse dispatch, HTML-escaped, footer links ---


def test_format_papers_escapes_and_links():
    out = format_papers(
        ["Q&A over graphs"],
        "week 27",
        board_url="https://notion.so/inbox?x=1&y=2",
        log_url="https://0xeljh.com/posts",
    )
    assert "Expedition dispatch — week 27" in out
    assert "1 sighting awaiting refinement" in out
    assert "• Q&amp;A over graphs" in out
    assert "https://notion.so/inbox?x=1&amp;y=2" in out  # & is HTML-escaped
    assert "https://0xeljh.com/posts" in out
