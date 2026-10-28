# Install NixOS on WSL

## Prerequisites

1. Windows 10/11 with WSL2 enabled
2. Download `nixos.wsl` from [NixOS-WSL releases](https://github.com/nix-community/NixOS-WSL/releases)

## Start NixOS

```powershell
wsl --install --from-file nixos.wsl
wsl -d NixOS
```

## Apply the configuration

Once inside NixOS-WSL:

```bash
git clone https://github.com/0xEljh/dotfiles.git ~/dotfiles
cd ~/dotfiles/nixos-config
sudo nixos-rebuild switch --flake .#contents-may-differ
```
