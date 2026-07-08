# Secrets

Encrypted `sops-nix` secret files, organized per host. Recipients are defined
in `nixos-config/.sops.yaml`; every file is encrypted to the user age key plus
the SSH-host-derived age key of the host that consumes it.

## Layout

- `sleeper-service/telegram-bot.env` — personal Telegram bot (dotenv)
- `sleeper-service/acme-namesilo.env` — NameSilo DNS-01 creds for ACME (dotenv)
- `sleeper-service/kodo-api.env` — kodo Go API env (dotenv)
- `darwin/nix-codesign.p12` — self-signed code-signing identity (binary), used
  to sign Yabai.app/Skhd.app with a stable, cert-pinned designated requirement
  so their Accessibility grant survives rebuilds. Consumed by
  `hosts/darwin/secrets.nix` + `modules/darwin/accessibility.nix`, which imports
  it into the **System keychain** at activation (root can't see the login
  keychain). Bootstrap once with `scripts/bootstrap-codesign-secret.sh`; the
  declaration is `pathExists`-guarded so the config evaluates before it exists.

Consumed via `hosts/<host>/secrets.nix` and referenced through
`config.sops.secrets.<name>.path` (`/run/secrets/<name>`).

## Editing

```sh
# from nixos-config/ — needs your age key in ~/.config/sops/age/keys.txt
sops secrets/sleeper-service/telegram-bot.env
```

Then rebuild; `restartUnits` restarts the consuming service automatically
(except acme, which picks changes up on its next timer run).

## Keys

- User key: `~/.config/sops/age/keys.txt` on sleeper-service (created
  2026-06-11). To edit secrets from another machine, generate a key there
  (`age-keygen -o ~/.config/sops/age/keys.txt`), add its public key to
  `.sops.yaml`, and run `sops updatekeys secrets/**/*.env`.
- Host keys come from each machine's `/etc/ssh/ssh_host_ed25519_key.pub` via
  `ssh-to-age`; sleeper-service, the WSL machine (tailnet `nixos`, inside
  central-node), and the MacBook (`host_darwin_macbook`, registered 2026-06-16)
  are registered. The Mac's key is read locally — its sshd need not be reachable.

## Outstanding

- **Rotate** the values that previously sat on disk in plaintext, then update
  via `sops`: Telegram bot token, Notion token, NameSilo API key, OpenRouter
  key, Supabase JWT secret, DB password in `DATABASE_URL`.
- Old plaintext files are still on disk as rollback fallback; delete after
  rotation: `~/.config/personal-telegram-bot/bot.env`, `~/.config/kodo/api.env`,
  `/var/lib/secrets/acme-0xeljh.env`.
- Migrate script-level `.env` usage (see design doc Phase 3) once service
  secrets have proven out.

Do not commit plaintext secrets here.
