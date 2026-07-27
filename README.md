# dotfiles

`nixos-config` is originally forked from [dustinlyons' nixos-config](https://github.com/dustinlyons/nixos-config), now trimmed to macOS (nix-darwin), WSL, and sleeper-service targets
`nvim` is built off lazyvim.
`scripts` contains my automations for time-accounting.

## Notable features

Some things that might be different from other public configs:

### 1) macOS “app persistence”

Problem: Nix store paths change on rebuild; macOS TCC (Accessibility permissions) tracks by code signature. Ad-hoc signatures include cdhash, which changes with every rebuild, silently revoking permissions.

Approach (two layers):

**GUI apps (kitty):** Finder aliases (not symlinks) into a stable folder via `mkalias`:
- Stable path: `/Applications/Nix Apps/kitty.app`
- Module: `nixos-config/modules/darwin/apps.nix`

**CLI daemons (yabai, skhd):** Minimal `.app` bundles + self-signed certificate:
- Stable paths: `/Applications/Yabai.app`, `/Applications/Skhd.app`
- Module: `nixos-config/modules/darwin/accessibility.nix`
- Certificate-based designated requirement (DR) uses cert identity, not cdhash — survives binary replacement across rebuilds
- `/usr/local/bin/` symlinks maintained for CLI convenience

**One-time prerequisite:** Create a self-signed code signing certificate:
1. Keychain Access > Certificate Assistant > Create a Certificate...
2. Name: `nix-codesign` | Identity Type: Self Signed Root | Certificate Type: Code Signing

On first run after `.#build-switch`, grant Accessibility once via
`System Settings > Privacy & Security > Accessibility`:

- `/Applications/Yabai.app`
- `/Applications/Skhd.app`
- `/Applications/Nix Apps/kitty.app`

These paths and signatures are stable across rebuilds.

### 2) Centralized + Extensible AI-tool configs (`ai-tools/`)

`ai-tools/` is the source-of-truth for agent instructions + shared skills, used by OpenCode, Claude Code, and Codex CLI.
These are global configs (separate from the local/project-based counterparts)

Inputs:

- Shared base: `ai-tools/shared/AI.md`
- OpenCode layer: `ai-tools/opencode/AGENTS.md`
- Claude layer: `ai-tools/claude-code/CLAUDE.md`
- Claude MCP: `ai-tools/claude-code/mcp.json`
- Codex layer: `ai-tools/codex/AGENTS.md`

Wiring:

- Home Manager activation: `nixos-config/modules/shared/ai-tools.nix`

What activation does:

- Links shared skills into each harness and adds pinned CLI-provided skills.
- -> Only need to maintain the global configs in this folder.
- Generates combined instruction files by concatenating `shared/AI.md` + tool layer, separated by `---`:
  - `~/.config/opencode/AGENTS.md`
  - `~/.claude/CLAUDE.md`
  - `~/.codex/AGENTS.md`
- Replaces only `~/.claude.json.mcpServers` from `ai-tools/claude-code/mcp.json`, preserving Claude's unrelated auth/state while reconciling removed servers.
- Installs the browser revision pinned by Playwright CLI and generates the skill matching the installed Hugging Face CLI.

Managed-search credentials stay in the untracked
`~/.config/ai-tools/secrets.env`; start from `ai-tools/secrets.env.example` and
set mode `0600`. Never set `EXA_API_KEY` globally because OpenCode's native Exa
path places it in the URL; the scoped MCP uses `EXA_MCP_API_KEY` in a header.
Parallel remains usable anonymously when `PARALLEL_MCP_API_KEY` is empty and
uses that key as a Bearer token when supplied.

### 2a) T3 Code remote agents (mobile + cross-device)

[T3 Code](https://github.com/pingdotgg/t3code) is a GUI front-end for Claude Code / OpenCode / Codex. This repo wires it up across all three hosts. Design: [`docs/design/ai-agent-mobile-refresh.md`](docs/design/ai-agent-mobile-refresh.md).

- **sleeper-service + WSL** run the exact T3 nightly pinned by `services.t3Serve.t3Version`. T3's built-in Tailscale integration exposes Tailnet-only HTTPS on port `443`.
- **OpenCode direct access** stays on `127.0.0.1:8779` and is proxied by Tailscale Serve at HTTPS port `8779`. `OPENCODE_SERVER_PASSWORD` is mandatory for this endpoint.
- **macOS** installs the ChatGPT, OpenCode, and T3 Code nightly desktop apps plus Tailscale through Homebrew casks.
- **Mobile / any device** can pair [https://app.t3.codes](https://app.t3.codes) with the HTTPS URL printed by T3. OpenCode's own interface is available at the same host's HTTPS port `8779`.
- T3 and OpenCode remain private to the Tailnet. Do not replace Serve with Funnel or publish either backend through the public nginx proxy.

One-time per host after first build:

- sleeper-service: `sudo tailscale up --ssh`
- macOS: launch Tailscale.app, sign in
- WSL: `sudo tailscale up` (Tailscale was already enabled here)
- Enable HTTPS certificates in the Tailscale admin console if the first `tailscale serve` invocation prints a consent URL.
- Add `OPENCODE_SERVER_USERNAME=opencode` and a strong `OPENCODE_SERVER_PASSWORD` to `~/.config/ai-tools/secrets.env`, then set the file mode to `0600`.

Then on the desktop client (Mac), pair via the QR / URL printed by the running `t3-serve` user service:
`journalctl --user -u t3-serve -f` on the host.

In each T3 environment's OpenCode provider settings, use
`http://127.0.0.1:8779` and the same OpenCode password. T3 stores this setting
in its own local state, so update it whenever the password is rotated.

Useful checks:

```bash
systemctl --user status t3-serve opencode-serve
tailscale serve status
journalctl --user -u t3-serve -u opencode-serve -f
```

### 3) `notion-cat`: `cat` to Notion

Simple utility to create a new Notion page and append stdin/files as code blocks.

Required env:

- NOTION_TOKEN
- NOTION_CAT_DATA_SOURCE_ID (Notion data source ID for the target database)

Examples:

- echo "hello" | notion-cat
- notion-cat README.md
- rg "TODO" -n . | notion-cat --title "TODO scan"
- notion-cat --suppress-output README.md  # omit document echo and success messages

May add other versions and integrate with ai-tools in the future.

---

## Nix on macOS

```bash
xcode-select --install
```

Use the official installer because determinate nix causes issues!

```bash
sh <(curl --proto '=https' --tlsv1.2 -L https://nixos.org/nix/install)
```

```bash
find apps/$(uname -m | sed 's/arm64/aarch64/')-darwin -type f \( -name apply -o -name build -o -name build-switch -o -name create-keys -o -name copy-keys -o -name check-keys -o -name rollback \) -exec chmod +x {} \;
```

```bash
nix run --extra-experimental-features 'nix-command flakes' .#apply
```

```bash
nix run --extra-experimental-features 'nix-command flakes' .#build
```

```bash
nix run --extra-experimental-features 'nix-command flakes' .#build-switch
```

---

## NixOS on WSL

For NixOS running in WSL, the target is `.#contents-may-differ` (on the box
itself a bare `--flake .` also resolves, since the attr matches the hostname):

```bash
sudo nixos-rebuild switch --flake .#contents-may-differ
```

To build without switching:

```bash
nixos-rebuild build --flake .#contents-may-differ
```
