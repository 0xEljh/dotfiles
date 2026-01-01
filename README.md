# dotfiles

`nixos-config` is forked from [dustinlyons' nixos-config](https://github.com/dustinlyons/nixos-config)
`nvim` is built off lazyvim.
`scripts` contains my automations for time-accounting.

---

## Installation for macOS

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
