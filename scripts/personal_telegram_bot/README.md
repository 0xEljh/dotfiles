# personal-telegram-bot

Telegram bot (`@nervous_energy_bot`) for sleeper-service. Design:
`docs/design/telegram-bot-sleeper-service.md`.

What it does:

- **On waking** — morning digest of open Bread tasks (due today + overdue),
  with a noon fallback if no wake event arrives.
- **Every 5 min** — health checks over hosted systemd units, public HTTPS
  endpoints, ActivityWatch data freshness (newest `aw-data` push < 26h), and
  disk usage (warning above 80%, critical above 90%); alerts on state
  transitions only (failure, escalation, and recovery).
- **09:00 daily** — checks the local Tailscale network map and reminds you when
  a visible device key has expired or will expire within 14 days.
- **:10 past each hour** — reports what the previous hour was classified as
  (Deep Work / Shallow Work, per `aw_notion_sync.py` thresholds) with a tool
  breakdown. Silent when unclassified.
- **Instant** — `OnFailure=` hooks on hosted services push an alert with a
  journal tail the moment a unit enters failed state.
- **Daemon** — long-polling command surface: `/status`, `/help`. Allowlisted
  user IDs only.

## Ops

```bash
# manual runs (from this directory)
uv run botctl send test
uv run botctl send morning --dry-run     # print, don't send
uv run botctl send morning --force       # bypass once-per-day dedupe
uv run botctl send health --force        # full summary, not just transitions
uv run botctl send tailscale-keys --dry-run

# tests
uv run pytest

# systemd
systemctl status personal-telegram-bot.service
systemctl list-timers 'personal-telegram-bot-*'
journalctl -u personal-telegram-bot-morning.service
```

Secrets live in `~/.config/personal-telegram-bot/bot.env` (chmod 600, not in
git): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_DEFAULT_CHAT_ID`,
`TELEGRAM_ALLOWED_USER_IDS`, `NOTION_TOKEN`, `NOTION_BREAD_DATASOURCE_ID`,
`TARGET_TZ`. Optional overrides: `HEALTH_SYSTEMD_UNITS`, `HEALTH_HTTP_URLS`,
`HEALTH_DISK_PATHS` (comma-separated), `HEALTH_DISK_WARNING_PERCENT`,
`HEALTH_DISK_CRITICAL_PERCENT`, `BOT_STATE_DB`, `AW_DATA_DIR`, and
`AW_DATA_MAX_AGE_HOURS`.
The Tailscale warning window is configurable with
`TAILSCALE_KEY_EXPIRY_WARNING_DAYS` (default: 14). The local status view covers
devices visible to sleeper-service; it is not an authoritative tailnet inventory.

State (digest dedupe, health transitions, audit log) is SQLite at
`~/.local/state/personal-telegram-bot/state.sqlite3`.
