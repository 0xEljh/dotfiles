{ pkgs }:

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

  # Cloud-related tools and SDKs
  docker
  docker-compose

  # Media-related packages
  ffmpeg
  fd
  noto-fonts
  noto-fonts-emoji
  meslo-lgs-nf

  # Node.js development tools
  nodejs_24
  bun

  # C tools
  gcc

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
  fzf
  difftastic
  nushell
  zsh-powerlevel10k

  # Python packages
  python3
  virtualenv
  uv
  ruff
  pyright
]
