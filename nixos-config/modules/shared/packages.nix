{ pkgs, lib ? pkgs.lib }:

with pkgs; [
  # General packages for development and system management
  kitty
  aspell
  aspellDicts.en
  bash-completion
  bat
  btop
  coreutils
  killall
  neofetch
  openssh
  sqlite
  wget
  zip
  gh
  lazygit

  # Encryption and security tools
  age
  age-plugin-yubikey
  gnupg
  libfido2
  magic-wormhole

  # Cloud-related tools and SDKs
  docker
  docker-compose

  # Media-related packages
  ffmpeg
  fd
  noto-fonts
  noto-fonts-color-emoji
  meslo-lgs-nf

  # Node.js development tools
  nodejs_24
  bun
  llm-agents.claude-code

  # C tools
  gcc

  # Lua
  lua5_1
  luarocks

  # Image processing (for image.nvim)
  imagemagick

  # Text and terminal utilities
  htop
  hunspell
  jetbrains-mono
  jq
  ripgrep
  tree
  tmux
  unrar
  unzip
  eza
  ouch
  zoxide
  atuin
  fzf
  difftastic
  nushell
  zsh-powerlevel10k
  notion-cat

  # Python packages
  python3
  virtualenv
  uv
  ruff
  pyright
] ++ lib.optionals (pkgs ? llm-agents && pkgs.llm-agents ? opencode) [ llm-agents.opencode ]
