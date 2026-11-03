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

Manual runs can use `~/.config/personal-telegram-bot/bot.env` (chmod 600, not in
git). This path's existence and readiness on sleeper are unverified. Sleeper's
systemd bot services use a root-managed SOPS environment file instead. Settings:
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_DEFAULT_CHAT_ID`,
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

## Opt in to agent notifications

The `notify_user` MCP tool sends one-way private-owner notifications. It does
not process replies or approvals. The host launcher is disabled by default.
Registration examples below are opt-in commands, not deployment instructions
that have already been executed.

### Prepare the sleeper launcher

Before enabling the launcher, have the operator confirm all of these prerequisites:

- The checkout at `/home/elijah/dotfiles/scripts/personal_telegram_bot` contains
  `botctl serve-notify-mcp` and its notification-only settings loader.
- Its `.venv` is already synchronized to the reviewed `uv.lock`, including the
  MCP dependency. Provision dependencies separately, not during MCP startup.
- An authoritative credential file is readable by the `elijah` SSH account
  without weakening existing secret permissions. Do not assume systemd's
  root-managed SOPS credentials are SSH-readable. If no suitable file exists,
  stop and choose a narrowly scoped credential-hosting service.
- Each client already has owner SSH access to `sleeper-service`, with a verified
  host key and noninteractive authentication. Do not copy an owner key solely
  for notifications. This access grants full shell authority, not notification-only access.

Set these Home Manager options for sleeper only after confirming readiness.
Replace the placeholder with the operator's fixed absolute runtime path:

```nix
services.agentNotifyMcp = {
	enable = true;
	environmentFile = "/absolute/operator-confirmed/bot.env";
};
```

This installs `/etc/profiles/per-user/elijah/bin/agent-notify-mcp`. The launcher
ignores caller arguments and environment overrides. It runs the fixed checkout
with `uv run --frozen --offline --no-sync`, disables dotenv auto-loading and
Python downloads, and uses the prepared `.venv`. Missing prerequisites fail
instead of installing packages. The runtime credential file is not copied into
the Nix store. Only its path is public configuration.

The notification loader reads only allowed notification settings from the fixed
`--env-file`, not inherited environment variables. Required settings are
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_DEFAULT_CHAT_ID`, and
`TELEGRAM_ALLOWED_USER_IDS`. Optional settings are `AGENT_NOTIFY_ATTEMPTS`,
`AGENT_NOTIFY_DEADLINE_SECONDS`, `AGENT_NOTIFY_BACKOFF_SECONDS`, and
`AGENT_NOTIFY_NETWORK_TIMEOUT_SECONDS`. The default call deadline is 60 seconds.
If the operator raises it, raise client timeouts to leave headroom.

### Register only the clients you choose

Run these examples from the project that needs notifications, after host setup.
They use the fixed SSH command with no PTY, no password prompts, strict host-key
checking, and an eight-second connection timeout. Protocol traffic uses stdin
and stdout. Keep remote shell startup output off stdout.

For a client running on sleeper, replace `ssh` and its arguments with the single
absolute launcher path. No Telegram credentials belong in client configuration.

#### OpenCode V2: project registration

```bash
opencode mcp add agent-notify -- ssh -T \
	-o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 \
	sleeper-service /etc/profiles/per-user/elijah/bin/agent-notify-mcp
```

In the project's `opencode.json` or `opencode.jsonc`, add the timeout to the
new entry. Preserve unrelated configuration:

```json
{
	"$schema": "https://opencode.ai/config.json",
	"mcp": {
		"servers": {
			"agent-notify": {
				"type": "local",
				"command": ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", "sleeper-service", "/etc/profiles/per-user/elijah/bin/agent-notify-mcp"],
				"timeout": { "startup": 90000, "execution": 90000 }
			}
		}
	}
}
```

Do not use `--global` here: Nix generates the global OpenCode config. V2 timeout
values are milliseconds and belong in an object, not a numeric V1 `timeout`.

#### Claude Code: local project registration

```bash
claude mcp add-json --scope local agent-notify \
	'{"type":"stdio","command":"ssh","args":["-T","-o","BatchMode=yes","-o","StrictHostKeyChecking=yes","-o","ConnectTimeout=8","sleeper-service","/etc/profiles/per-user/elijah/bin/agent-notify-mcp"],"timeout":90000}'
MCP_TIMEOUT=90000 claude
```

The per-server `timeout` bounds tool calls. `MCP_TIMEOUT` applies to client
startup, not to the SSH server's environment. Local scope avoids the user-wide
MCP map that Home Manager replaces. Do not add this to the generated global map.

#### Codex: one invocation, no global MCP registration

```bash
codex \
	-c 'mcp_servers.agent-notify.command="ssh"' \
	-c 'mcp_servers.agent-notify.args=["-T","-o","BatchMode=yes","-o","StrictHostKeyChecking=yes","-o","ConnectTimeout=8","sleeper-service","/etc/profiles/per-user/elijah/bin/agent-notify-mcp"]' \
	-c 'mcp_servers.agent-notify.startup_timeout_sec=90' \
	-c 'mcp_servers.agent-notify.tool_timeout_sec=90'
```

These overrides apply to this invocation. They preserve the no-global-MCP policy.
For persistent project opt-in, put the equivalent `[mcp_servers.agent-notify]`
table in that trusted project's `.codex/config.toml`, not `~/.codex/config.toml`.

### Check discovery without sending

After authorized setup, inspect `opencode mcp list`, Claude's `/mcp`, or Codex's
`/mcp`. Confirm `agent-notify` connects and exposes `notify_user`. Discovery does
not send a notification. Do not call `notify_user` until live delivery is
authorized. `report-plain --validate-only` is not an offline check: it calls Telegram.

For a handoff, include what is ready or blocked and the next step. Omit secrets,
private logs, full source files, pairing grants, and credential-bearing links.
Telegram can display the message on a lock screen. Use known source labels and
HTTPS links only. A missing link is acceptable.

Treat `sent` as Telegram acceptance, not proof of attention. Repeated calls,
including calls with the same reference, can send again. `unknown` or a broken
connection can mean delivery occurred. Honor retry delays and avoid unbounded
retries. Report failures in the originating conversation, not recursively through
the notification tool.

Registration syntax and timeout fields were checked against the official
[OpenCode V2 MCP guide](https://opencode.ai/v2/docs/mcp-servers),
[Claude Code MCP guide](https://code.claude.com/docs/en/mcp), and
[Codex MCP guide](https://developers.openai.com/codex/mcp/).
Live SSH, credential access, tool discovery, delivery, and phone behavior remain
separate operator checks.
