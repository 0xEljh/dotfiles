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

- Home Manager activation: `nixos-config/modules/shared/ai-tools.nix`
- OpenCode server config: `nixos-config/modules/shared/config/opencode.json`
- OpenCode terminal config: `ai-tools/opencode/cli.json`

Managed-search credentials stay in the untracked
`~/.config/ai-tools/secrets.env`; start from `ai-tools/secrets.env.example` and
set mode `0600`. Never set `EXA_API_KEY` globally because OpenCode's native Exa
path places it in the URL; the scoped MCP uses `EXA_MCP_API_KEY` in a header.
Parallel remains usable anonymously when `PARALLEL_MCP_API_KEY` is empty and
uses that key as a Bearer token when supplied.

### 2a) T3 Code remote agents (mobile + cross-device)

[T3 Code](https://github.com/pingdotgg/t3code) is a GUI front-end for Claude Code, OpenCode, and Codex.

All host outputs use OpenCode V2. Package and service pins live in
[`ai-tools.nix`](nixos-config/modules/shared/ai-tools.nix) and
[`t3-serve.nix`](nixos-config/modules/shared/t3-serve.nix).

- OpenCode's direct interface is available at the same host's HTTPS port `8779`. `OPENCODE_SERVER_PASSWORD` is mandatory for this endpoint.
- T3 stays Tailnet-only until each host is linked interactively. Linking enables an authenticated public T3 Connect tunnel for that host; it does not expose direct OpenCode. Do not use Funnel or the public nginx proxy for either backend.

One-time per host after first build:

- sleeper-service: `sudo tailscale up --ssh`
- macOS: launch Tailscale.app, sign in
- WSL: `sudo tailscale up` (Tailscale was already enabled here)
- Enable HTTPS certificates in the Tailscale admin console if the first `tailscale serve` invocation prints a consent URL.
- Add a strong `OPENCODE_SERVER_PASSWORD` to `~/.config/ai-tools/secrets.env`, then set the file mode to `0600`. The username is always `opencode`.

Then on the desktop client (Mac), pair via the QR / URL printed by the running `t3-serve` user service:
`journalctl --user -u t3-serve -f` on the host.

T3 manages its own loopback OpenCode servers and credentials. The direct
OpenCode endpoint's password is not a T3 pairing credential.

Useful checks:

```bash
systemctl --user status t3-serve opencode-serve
tailscale serve status
journalctl --user -u t3-serve -u opencode-serve -f
```

### 3) `notion-cat`: `cat` to Notion

Create a new Notion page from Markdown files, or capture stdin/code as literal
code blocks. Markdown supports native text blocks, local images, and explicit
HTML artifacts. Each invocation creates a new page; it does not update one.

Required environment variables for publication (not dry-run):

- NOTION_TOKEN
- NOTION_CAT_DATA_SOURCE_ID (Notion data source ID for the target database)

Examples:

- echo "hello" | notion-cat
- notion-cat README.md
- rg "TODO" -n . | notion-cat --title "TODO scan"
- notion-cat --suppress-output README.md  # omit document echo, but print the page URL

Local images use an image-only paragraph, such as
`![Diagram](./artifacts/diagram.svg)`. To insert an interactive HTML preview among
native text blocks, put one unquoted local path in a `notion-html` fence:

````markdown
## Interactive diagram

```notion-html
./artifacts/diagram.html
```

The controls explore the selected state.
````

Spell the `notion-html` marker exactly. Escaped spellings and extra fence
arguments remain ordinary code blocks.

Relative paths use the Markdown file's directory. HTML fence paths are literal:
spaces and percent signs are not URL-encoded or decoded. Surrounding whitespace
is trimmed. `.html` and `.htm` files upload as Notion-hosted HTML embeds. Only the
referenced file is uploaded; bundle its scripts, styles, and images beforehand
when it needs to work without external resources. Notion applies its own sandbox,
so check interactions and layout in the resulting page.

Inspect planned uploads without credentials or API calls:
`notion-cat --dry-run --suppress-output report.md`. This loads environment files
but does not validate live workspace limits or render HTML.

Use `--html=literal` to leave artifact fences as code without reading their target
files. `--images=alt` renders image descriptions without uploading images; it does
not disable HTML artifacts. Combine both for publication without referenced
asset reads. Ordinary `html` fences, raw HTML markup, HTML hyperlinks, and
standalone HTML file arguments remain literal. Use an outer longer Markdown
fence, as above, to document the syntax without uploading that example.

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
cd ~/dotfiles/nixos-config
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

Rebuilds go through [nh](https://github.com/nix-community/nh), which adds a
readable build view and prints a generation diff after every switch. The flake
apps are the same verbs darwin uses:

```bash
cd ~/dotfiles/nixos-config
nix run .#build-switch   # build + activate + generation diff
nix run .#build          # build only
nix run .#rollback       # roll back to the previous generation
```

Or directly, from anywhere (`NH_FLAKE` points at this repo):

```bash
nh os switch
nh os info               # list generations
```

The hostname (`contents-may-differ`) matches the flake attribute, so nh selects
the right `nixosConfigurations` entry with no `-H` flag. The underlying raw form
still works if needed:

```bash
sudo nixos-rebuild switch --flake .#contents-may-differ
```

## Linting and formatting nix

```bash
cd ~/dotfiles/nixos-config
nix run .#lint           # statix + deadnix (optional path arg, default .)
nix fmt                  # nixfmt via nixfmt-tree
```

Both are also on `PATH` inside the repo via direnv (`use flake ./nixos-config`).
