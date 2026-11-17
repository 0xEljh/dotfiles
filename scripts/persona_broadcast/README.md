# Persona Broadcast MCP

One MCP endpoint controls every configured native Persona desktop. Callers do not select a computer. Each control is attempted on all devices concurrently, including when another device is offline.

## Run

Requires Bun 1.3.13 and Node 24 or later.

```bash
bun install --frozen-lockfile
bun run check
bun run start -- config.example.json
```

The default client endpoint is **`http://127.0.0.1:47835/mcp` on the machine running the service**. It is stateless Streamable HTTP with JSON responses. No session header is required. Configure an MCP client to use that single URL, not the per-device relay URLs.

The example configuration targets the Windows relay on local port 47832 and the macOS relay on local port 47834. The gateway does not install, wake, restart, or reconnect those applications or tunnels. Retired port 47833 is not used.

Run `bun run start -- /absolute/path/to/config.json` to choose another configuration. Device endpoints must be unique literal `http://127.0.0.1:PORT/mcp` URLs without credentials or query parameters. Device IDs must be unique lowercase names. The listener cannot target itself.

Configuration uses these fields (also in `config.example.json`):

```json
{
  "port": 47835,
  "deadlineMs": 2500,
  "cleanupMs": 300,
  "maxConcurrentPerDevice": 4,
  "devices": [
    {"id":"windows","endpoint":"http://127.0.0.1:47832/mcp"},
    {"id":"macos","endpoint":"http://127.0.0.1:47834/mcp"}
  ]
}
```

Port `0` selects an ephemeral loopback port for isolated testing. The startup JSON log reports its actual endpoint. Work and cleanup durations are positive integer milliseconds. `maxConcurrentPerDevice` is optional and defaults to 4.

## Tools

| Tool | Arguments | Behavior |
| --- | --- | --- |
| `play_animation` | `{"animation":"speaking"}` | Play the action on every desktop where it is installed. No audio. |
| `control_window` | `{"action":"show"}` | Broadcast `show`, `hide`, or `toggle`. Toggle flips each window independently. |
| `list_animations` | `{}` | Read every reachable desktop's live catalog. Any discovered action can be attempted. |
| `get_status` | `{}` | Read each desktop's current state and availability. |

These tools have no device-selector parameter. Generation and library editing are not exposed. Their job and asset identifiers belong to individual desktops.

## Results

The text content contains JSON. `structuredContent` contains the same object. Each device's full backend tool result is nested under `result`.

```json
{
  "outcome": "partial",
  "uncertain": false,
  "devices": [
    {"device":"windows","status":"ok","result":{"content":[{"type":"text","text":"Persona is playing the speaking action."}]}},
    {"device":"macos","status":"unavailable","error":"Device deadline exceeded."}
  ],
  "note": "Best effort. …"
}
```

- `complete`: every device acknowledged success.
- `partial`: at least one succeeded, but not all did.
- `uncertain`: none acknowledged success, and a submitted control might have executed.
- `failed`: none succeeded, and no control has uncertain delivery.

Per-device status is `ok`, `rejected`, `unavailable`, or `unknown`. `rejected` preserves an explicit backend tool error. `unknown` means a control was submitted but its acknowledgment was lost. Failed reads are unavailable, not uncertain. The separate `uncertain` flag can also be true for a partial result.

**Do not automatically retry partial or uncertain controls.** A desktop might already have acted, even when the top-level MCP `isError` is true. Repeating toggle could undo the first action. The gateway never retries or queues controls. An already buffered request can still execute late. There is no rollback, synchronized playback, or ordering guarantee between different callers.

The default work deadline is 2500 ms and the cleanup budget is another 300 ms. A sleeping device can therefore make a call take about 2.8 seconds. There is no cached-health shortcut. An acknowledgment is not proof of rendered motion.

## Capacity and cleanup

`maxConcurrentPerDevice` defaults to 4, including session cleanup. Excess requests receive an immediate busy result for that device without dispatch or queuing. This leaves room below Persona's eight-session limit. You can override the setting, but more overlap can cause backend session eviction. Other direct clients also consume capacity.

Each attempt creates a fresh backend session, closes its local transport, and tries a bounded session DELETE. `cleanup: "unconfirmed"` does not invalidate an acknowledged control. A session whose initialization response was lost cannot always be deleted. Persona's own session expiry and capacity rules still apply.

Disconnecting a caller or stopping the gateway cancels local work and cleans up known sessions. It cannot undo a control already delivered. A new request uses fresh backend connections, so a recovered tunnel does not require restarting the gateway.

## Operate the server deployment

The deployed copy on sleeper-service is `~/.local/share/persona-broadcast`. The transient user unit is `persona-broadcast.service`.

```bash
systemctl --user status persona-broadcast
journalctl --user -u persona-broadcast -n 50
systemctl --user stop persona-broadcast
```

The transient unit is not enabled at boot. To launch it again after a reboot, from the deployed directory:

```bash
systemd-run --user --unit=persona-broadcast \
  --working-directory="$HOME/.local/share/persona-broadcast" \
  --property=Restart=on-failure --property=RestartSec=2s \
  --property=TimeoutStopSec=10s \
  "$(command -v node)" "$HOME/.local/share/persona-broadcast/dist/main.js" \
  "$HOME/.local/share/persona-broadcast/config.example.json"
```

`GET /health` reports process liveness only. Use MCP `get_status` for device availability. Startup must not depend on an online desktop.

The listener binds to IPv4 loopback only and requires a matching loopback Host. Missing Origin is allowed. A supplied Origin must be this listener's HTTP loopback or localhost origin. POST bodies are limited to 64 KiB. GET and DELETE at `/mcp` return 405 because there are no frontend sessions or event streams.

This endpoint is unauthenticated: processes on the same server are trusted callers. Do not publish it through a wildcard listener, public proxy, or Tailscale Serve. Register it in agent clients only where that server-local trust boundary is appropriate.
