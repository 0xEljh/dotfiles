# dotfiles

`nixos-config` is forked from [dustinlyons' nixos-config](https://github.com/dustinlyons/nixos-config)
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

`ai-tools/` is the source-of-truth for agent instructions + shared skills, used by both OpenCode and Claude Code.
These are global configs (separate from the local/project-based counterparts)

Inputs:

- Shared base: `ai-tools/shared/AI.md`
- OpenCode layer: `ai-tools/opencode/AGENTS.md`
- Claude layer: `ai-tools/claude-code/CLAUDE.md`

Wiring:

- Home Manager activation: `nixos-config/modules/shared/ai-tools.nix`

What activation does:

- Symlinks shared skills into both global config files
- -> Only need to maintain the global configs in this folder.
- Generates combined instruction files by concatenating `shared/AI.md` + tool layer, separated by `---`:
  - `~/.config/opencode/AGENTS.md`
  - `~/.claude/CLAUDE.md`

Note: `ai-tools/opencode/opencode.json` and `ai-tools/claude-code/settings.json` are currently uninitialised; if missing, activation warns and proceeds on best-effort.

### 3) `notion-cat`: `cat` to Notion

Simple utility to create a new Notion page and append stdin/files as code blocks.

Required env:

- NOTION_TOKEN
- NOTION_CAT_DATA_SOURCE_ID (Notion data source ID for the target database)

Examples:

- echo "hello" | notion-cat
- notion-cat README.md
- rg "TODO" -n . | notion-cat --title "TODO scan"

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

For NixOS running in WSL, you need to specify the `.#wsl` target when building:

```bash
sudo nixos-rebuild switch --flake .#wsl
```

To build without switching:

```bash
nixos-rebuild build --flake .#wsl
```
