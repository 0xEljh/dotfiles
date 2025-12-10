# WSL Module

NixOS configuration for Windows Subsystem for Linux (WSL2).

## Layout

```
.
├── home-manager.nix   # User programs and WSL-specific home config
├── packages.nix       # Packages for WSL environment
└── README.md          # This file
```

## Features

- **win32yank** - Clipboard integration between WSL and Windows
- **WSLg support** - GUI application support via Wayland
- **Neovim** - Pre-configured editor with WSL clipboard integration
- **Shared config** - Inherits from `modules/shared/` for consistency

## Installation

### Prerequisites

1. Windows 10/11 with WSL2 enabled
2. Download `nixos.wsl` from [NixOS-WSL releases](https://github.com/nix-community/NixOS-WSL/releases)

### Quick Start

```powershell
# Install NixOS-WSL (PowerShell)
wsl --install --from-file nixos.wsl

# Start NixOS
wsl -d NixOS
```

### Apply Configuration

Once inside NixOS-WSL:

```bash
# Clone your dotfiles
git clone https://github.com/0xEljh/dotfiles.git ~/.dotfiles

# Navigate to nixos-config
cd ~/.dotfiles/nixos-config

# Update flake lock (first time)
nix flake update

# Build and switch to the WSL configuration
sudo nixos-rebuild switch --flake .#wsl
```

## Clipboard Integration

The configuration includes `win32yank` for seamless clipboard sharing between WSL and Windows.

### Neovim Clipboard

Neovim clipboard is configured in your dotfiles at `nvim/lua/config/options.lua`. It automatically detects WSL and uses `win32yank.exe` for clipboard operations.

### Manual Clipboard Operations

```bash
# Copy to Windows clipboard
echo "text" | win32yank.exe -i

# Paste from Windows clipboard
win32yank.exe -o
```

## Dotfiles Symlink Automation

The home-manager config automatically symlinks your dotfiles on each rebuild:

- `~/.dotfiles/nvim` → `~/.config/nvim`

To add more symlinks, edit `modules/wsl/home-manager.nix` and add calls to `link_config` in the activation script.

**Important:** Clone your dotfiles to `~/.dotfiles` before running `nixos-rebuild`:

```bash
git clone https://github.com/0xEljh/dotfiles.git ~/.dotfiles
```

## GUI Applications (WSLg)

WSLg is enabled by default in modern WSL2. GUI apps should work out of the box.

To test:
```bash
# If you have a GUI app installed
firefox &
```

## TODO

See comments in `hosts/wsl/default.nix` for optional enhancements:
- SSH key configuration
- Syncthing for file sync
- Additional GUI applications
- Podman as Docker alternative
