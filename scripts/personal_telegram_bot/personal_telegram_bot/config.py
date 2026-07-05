from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .providers import aw_hours

DEFAULT_ENV_FILE = Path.home() / ".config" / "personal-telegram-bot" / "bot.env"
DEFAULT_DB_PATH = Path.home() / ".local" / "state" / "personal-telegram-bot" / "state.sqlite3"
# Phone telemetry lives in its own database: different lifecycle (telemetry vs
# bot bookkeeping), and it keeps the hot WAL file out of the git-synced repo.
DEFAULT_LIFE_DB_PATH = (
    Path.home() / ".local" / "state" / "personal-telegram-bot" / "life_events.sqlite3"
)

# Units and endpoints hosted on sleeper-service; override with
# HEALTH_SYSTEMD_UNITS / HEALTH_HTTP_URLS (comma-separated).
DEFAULT_HEALTH_UNITS = [
    "nginx.service",
    "kodo-api.service",
    "kodo-ml.service",
    "vamp-tutor-backend.service",
    "vamp-tutor-website.service",
    "digital-garden.service",
    "tea-the-gathering.service",
    "docker-vamp-tutor-postgres.service",
]
DEFAULT_HEALTH_URLS = [
    "https://0xeljh.com",
    "https://teathegathering.com",
    "https://vamptutor.com",
]


def parse_user_ids(raw: str) -> frozenset[int]:
    return frozenset(int(part) for part in raw.split(",") if part.strip())


def _parse_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass(frozen=True)
class Config:
    telegram_token: str
    default_chat_id: int
    allowed_user_ids: frozenset[int]
    notion_token: str | None
    bread_datasource_id: str | None
    tz: ZoneInfo
    db_path: Path
    health_units: list[str]
    health_urls: list[str]
    aw_data_dir: Path
    aw_max_age_hours: float
    aw_systematic_after_hours: float
    aw_stale_reminder_hours: int
    life_db_path: Path
    life_ingest_token: str | None
    life_ingest_bind: str
    life_ingest_port: int
    # Optional URL of the Bread board, linked in the morning digest footer.
    bread_url: str | None = None
    # Local-hour window [floor, ceiling) the wake-triggered morning digest may
    # fire in; outside it the noon fallback timer sends the digest. Filters
    # pre-dawn SAA stirs / early alarms and late-morning nap-stops.
    wake_gate_hour: int = 7
    wake_gate_hour_end: int = 11
    # Evening standdown deep link: the day's Time-Accounting page when the
    # Time-Accountant integration is configured (NOTION_TIME_ACCOUNTANT_SECRET +
    # NOTION_TIME_ACCOUNTING_DATASOURCE_ID), else the static database URL below.
    time_accounting_url: str | None = None
    time_accountant_secret: str | None = None
    time_accounting_datasource_id: str | None = None
    # Optional Paper Inbox (weekly papers dispatch): datasource of captured
    # papers awaiting refinement, plus the board URL for the dispatch footer.
    # Auth reuses NOTION_TOKEN; unset means the dispatch is a no-op.
    paper_inbox_datasource_id: str | None = None
    paper_inbox_url: str | None = None
    # TPOT social assistant: inference endpoint from docs/design/06 and the
    # WakaTime key needed for per-project topic construction.
    tpot_inference_url: str | None = None
    tpot_inference_token: str | None = None
    wakatime_api_key: str | None = None
    tpot_waka_min_minutes: int = 45

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        if env is None:
            # systemd injects the env file via EnvironmentFile; for manual runs
            # fall back to loading it ourselves (never overriding real env).
            env_file = os.environ.get("PERSONAL_TELEGRAM_BOT_ENV", str(DEFAULT_ENV_FILE))
            load_dotenv(env_file)
            env = os.environ

        token = env.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise SystemExit("TELEGRAM_BOT_TOKEN is not set (expected in bot.env)")
        chat_id = env.get("TELEGRAM_DEFAULT_CHAT_ID", "")
        if not chat_id:
            raise SystemExit("TELEGRAM_DEFAULT_CHAT_ID is not set (expected in bot.env)")

        return cls(
            telegram_token=token,
            default_chat_id=int(chat_id),
            allowed_user_ids=parse_user_ids(env.get("TELEGRAM_ALLOWED_USER_IDS", "")),
            notion_token=env.get("NOTION_TOKEN") or None,
            bread_datasource_id=env.get("NOTION_BREAD_DATASOURCE_ID") or None,
            tz=ZoneInfo(env.get("TARGET_TZ", "Asia/Singapore")),
            db_path=Path(env.get("BOT_STATE_DB", str(DEFAULT_DB_PATH))),
            health_units=_parse_csv(env.get("HEALTH_SYSTEMD_UNITS", ",".join(DEFAULT_HEALTH_UNITS))),
            health_urls=_parse_csv(env.get("HEALTH_HTTP_URLS", ",".join(DEFAULT_HEALTH_URLS))),
            aw_data_dir=Path(env.get("AW_DATA_DIR", str(aw_hours.DEFAULT_AW_DATA_DIR))),
            aw_max_age_hours=float(env.get("AW_DATA_MAX_AGE_HOURS", str(aw_hours.DEFAULT_MAX_AGE_HOURS))),
            aw_systematic_after_hours=float(
                env.get("AW_SYSTEMATIC_AFTER_HOURS", str(aw_hours.DEFAULT_SYSTEMATIC_AFTER_HOURS))
            ),
            aw_stale_reminder_hours=int(
                env.get("AW_STALE_REMINDER_HOURS", str(aw_hours.DEFAULT_STALE_REMINDER_HOURS))
            ),
            life_db_path=Path(env.get("LIFE_DB", str(DEFAULT_LIFE_DB_PATH))),
            life_ingest_token=env.get("LIFE_INGEST_TOKEN") or None,
            # nginx (hooks.0xeljh.com) is the sole entrypoint and terminates
            # TLS, so the app binds loopback only — no direct public/tailnet port.
            life_ingest_bind=env.get("LIFE_INGEST_BIND", "127.0.0.1"),
            life_ingest_port=int(env.get("LIFE_INGEST_PORT", "8830")),
            bread_url=env.get("NOTION_BREAD_URL") or None,
            wake_gate_hour=int(env.get("WAKE_GATE_HOUR", "7")),
            wake_gate_hour_end=int(env.get("WAKE_GATE_HOUR_END", "11")),
            time_accounting_url=env.get("NOTION_TIME_ACCOUNTING_URL") or None,
            time_accountant_secret=env.get("NOTION_TIME_ACCOUNTANT_SECRET") or None,
            time_accounting_datasource_id=env.get("NOTION_TIME_ACCOUNTING_DATASOURCE_ID") or None,
            paper_inbox_datasource_id=env.get("NOTION_PAPER_INBOX_DATASOURCE_ID") or None,
            paper_inbox_url=env.get("NOTION_PAPER_INBOX_URL") or None,
            tpot_inference_url=env.get("TPOT_INFERENCE_URL") or None,
            tpot_inference_token=env.get("TPOT_INFERENCE_TOKEN") or None,
            wakatime_api_key=env.get("WAKATIME_API_KEY") or None,
            tpot_waka_min_minutes=int(env.get("TPOT_WAKA_MIN_MINUTES", "45")),
        )
