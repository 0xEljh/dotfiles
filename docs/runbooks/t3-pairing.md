# Pairing a device with `t3 serve`

How to connect a client (phone, tablet, desktop app, or browser) to a running
`t3 serve` instance on the tailnet. Applies to **sleeper-service** and
**contents-may-differ** — each runs its own server with its own credential store,
so you pair per-server.

## The model (read this once)

Two different credentials are involved, and conflating them is the usual source of
confusion:

| Credential       | Who holds it | Lifetime                          | Reusable? |
|------------------|--------------|-----------------------------------|-----------|
| **Pairing token**| you, briefly | until its TTL expires **or** it's redeemed once | **No — one-time** |
| **Session**      | the client   | until cleared / revoked / expires | yes, that's the point |

```
mint pairing token  ──>  open pairing URL on device  ──>  server checks token,
(on the server)          http://<tailnet-ip>:3773/pair    marks it used, issues
                         #token=XXXX                       the device a SESSION
                                                                   │
                         every later visit ◀────────────────────── ┘
                         uses the stored session (no token needed)
```

- A pairing token is a **one-time coupon**. Redeem it once → it's spent.
- The **TTL** is just how long the *unused* coupon stays valid (default 5 min).
  We use a long TTL so you can mint a link now and redeem it whenever.
- After pairing, the device authenticates with its **session** and does not need
  another token until that session is lost.
- Therefore: **one token per device.** Phone = one link, laptop = another link.

Auth is always on when the server is bound to a tailnet (non-loopback) address —
there is no "no-auth" mode. The tailnet is the outer boundary; pairing is the inner
one.

## Pair a device

1. **Find the server's tailnet IP** (it can change; the server resolves it at start):

   ```sh
   tailscale ip -4 | head -n1
   # or read it from the server log:
   journalctl --user -u t3-serve -o cat | grep -m1 'Listening on'
   ```

2. **Mint a long-lived pairing link** on that server (as `elijah`):

   ```sh
   npx -y file:/home/elijah/.local/share/t3/t3-0.0.28-nightly.20260621.614-pr2673.0.tgz \
     auth pairing create --ttl 3650d --label phone \
     --base-url http://<tailnet-ip>:3773 --json
   ```

   - `--label` names the grant so `pairing list` is legible later (`phone`, `macbook`, …).
   - `--ttl 3650d` ≈ 10 years; use any duration (`30d`, `1h`, …).
   - The store is the default `$HOME/.t3` (`~/.t3/userdata/state.sqlite`) — the same
     one `t3 serve` reads — so **no `--base-dir` flag is needed**.
   - Output JSON contains `pairingUrl`, e.g.
     `http://100.65.176.86:3773/pair#token=XXXXXXXX`.

3. **Open the `pairingUrl` on the device**, while it's on the tailnet. The t3 web UI
   loads from the server, consumes the token, and establishes a session. Done — that
   device won't need a token again until its session is cleared.

> Treat the pairing URL like a password: anyone who can reach the server on the
> tailnet **and** has the link can create a session (= run agents as `elijah`).
> Keep spare links in a password manager, not in chat logs.

## Manage / revoke

```sh
T3="npx -y file:/home/elijah/.local/share/t3/t3-0.0.28-nightly.20260621.614-pr2673.0.tgz"

$T3 auth pairing list --json          # active grants (token values never shown)
$T3 auth pairing revoke <id>          # kill a leaked/unused link (id from list)
```

If a device starts failing with `Invalid session token payload` in the server log,
its session went bad — just pair it again with a fresh link.

## Notes

- **Per-server.** A link minted on sleeper-service only pairs clients to
  sleeper-service. Repeat on contents-may-differ (when WSL is running) for that box.
- **Startup QR / token.** `t3 serve` also prints a `Pairing URL` + QR code at startup,
  but that token is short-lived (5 min) — fine for an immediate pair, not for later.
- **No Telegram delivery.** Auto-delivering tokens to Telegram was tried and removed
  (see `docs/design/t3-pairing-telegram.md`, status SHELVED). Long-lived links +
  durable sessions make on-demand delivery unnecessary.
