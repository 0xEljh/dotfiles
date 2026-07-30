# Runbook — MacroDroid phone telemetry (re-setup)

After a MacroDroid reinstall/reset the **`phone`** event stream stops (OwnTracks
location and Sleep-as-Android are separate apps and keep working). This restores
it from scratch, now with a **screen-off** signal so the usage reducer stops
attributing idle time to whatever app was open when you pocketed the phone.

All macros POST JSON to the public ingest endpoint:

```
https://hooks.0xeljh.com/ingest/phone/<TOKEN>
```

- `<TOKEN>` is `LIFE_INGEST_TOKEN` (from sops `telegram-bot.env`; do not paste it
  into anything shared).
- Method **POST**, header `Content-Type: application/json`.
- `ts` is **optional** — omit it and the server stamps receive time, which is
  fine since events arrive in near-real-time. Include it only if you want exact
  phone-clock timestamps (ISO-8601, e.g. `2026-06-28T21:05:00`).

Exempt MacroDroid from battery optimization (Settings → Battery → Unrestricted),
or Android will throttle the triggers.

---

## Macro 1 — App foreground (per-app usage)

The core usage signal: one POST per app switch; the reducer infers each app's
duration from the gap to the next event.

- **Trigger:** *Application Launched* → **All applications** (Pro also exposes a
  "Foreground app changed" trigger; either works).
- **Action:** *HTTP Request* (POST), URL above, body:

```json
{ "event": "app_foreground", "app": "{app_name}", "package": "{package_name}" }
```

Insert MacroDroid's magic-text variables for the launched app's **name** and
**package** (names vary by trigger — e.g. `[app_name]` / the package variable the
*Application Launched* trigger exposes). `app` is **required**; `package` is
optional but improves launcher/system-UI filtering.

## Macro 2 — Screen on/off (the idle fix)

Without this, the last app before the screen turns off keeps accruing time until
your next pickup (capped, but still inflated). Sending `screen_off` lets the
reducer close that session exactly.

- **Triggers:** *Device Events → Display OFF* and *Display ON* (two triggers, one
  macro, or two macros).
- **Action:** *HTTP Request* (POST), same URL, body:

```json
{ "event": "screen_off" }
```

```json
{ "event": "screen_on" }
```

(`screen_on` is also stored — it's the future "pickup" signal for
first-pickup-after-wake; harmless now.)

---

## Verify

1. From the phone, trigger an app switch and a screen off/on.
2. On the server, confirm events landed (no token needed):

```
botctl phone-summary --date $(date +%F) --json
```

   You should see apps under `hours`, and a fresh `screen_off`/`screen_on` will
   show up as boundaries (they carry no app time of their own).
3. Or watch ingest logs: `journalctl -u personal-telegram-bot-ingest -f`
   (the token is redacted to `<token>` in the logs).

## Notes

- The endpoint accepts `event` ∈ `app_foreground` (needs `app`), `screen_on`,
  `screen_off`, `unlocked`. Anything else 400s.
- Redelivery is idempotent per (event, second, app), so an occasional retry macro
  is safe.
- This stream feeds the Notion day page (hourly tags) and the evening standdown's
  link target — see `docs/design/time-accounting-next-steps.md`.
