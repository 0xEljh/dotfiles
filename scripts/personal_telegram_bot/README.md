# personal-telegram-bot

Telegram bot (`@nervous_energy_bot`) for sleeper-service.

- Commands: [bot.py](personal_telegram_bot/bot.py)
- Services and schedules: [telegram-bot.nix](../../nixos-config/hosts/sleeper-service/services/telegram-bot.nix)

## Ops

```bash
# manual runs (from this directory)
uv run botctl send test
uv run botctl send morning --dry-run     # print, don't send
uv run botctl send morning --force       # bypass once-per-day dedupe
uv run botctl send health --force        # full summary, not just transitions
uv run botctl send disk-health --host contents-may-differ --dry-run
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

On `contents-may-differ`, `personal-telegram-bot-disk-health.service` reads its
bot token and chat ID from the SOPS-managed
`/run/secrets/telegram-disk-alert.env` and keeps independent transition state at
`/var/lib/personal-telegram-bot-disk-health/state.sqlite3`.
