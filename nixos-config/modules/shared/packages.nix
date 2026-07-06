{ pkgs, fff, lib ? pkgs.lib }:

let
  fffPackages = fff.packages.${pkgs.stdenv.hostPlatform.system};
in
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
  fastfetch
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
  rclone

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
  tree-sitter

  # Lua
  lua5_1
  luarocks
  stylua

  # Image processing (for image.nvim)
  imagemagick

  # Text and terminal utilities
  htop
  hunspell
  jq
  ripgrep
  ast-grep
  tree
  tmux
  unrar
  unzip
  eza
  ouch
  zoxide
  atuin
  fzf
  fffPackages.fff-mcp
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

  # Secret management
  sops
]
++ lib.optionals stdenv.hostPlatform.isLinux [
  # Browser automation for Playwright MCP on Linux hosts.
  chromium
]
++ lib.optionals (pkgs ? llm-agents && pkgs.llm-agents ? opencode) [ llm-agents.opencode ]
