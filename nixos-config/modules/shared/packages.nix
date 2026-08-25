{ pkgs, fff, lib ? pkgs.lib }:

let
  context7Cli = pkgs.callPackage ../../packages/context7-cli { };
  fffPackages = fff.packages.${pkgs.stdenv.hostPlatform.system};
  playwrightCli = pkgs.callPackage ../../packages/playwright-cli { };
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
  # lazygit, fzf, zoxide and atuin are installed by their home-manager
  # modules (see modules/shared/home-manager.nix) so their shell hooks and
  # config files come with them.

  # Nix ergonomics. nh itself arrives via programs.nh; nix-tree is here
  # because it is useful against any closure, not just this repo. The linters
  # (statix/deadnix/nixfmt) are repo-scoped in the flake devShell instead.
  nix-tree

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
  llm-agents.codex
  context7Cli
  playwrightCli

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
  ty
  python3Packages.huggingface-hub

  # Secret management
  sops
]
++ lib.optionals (pkgs ? llm-agents && pkgs.llm-agents ? opencode) [ llm-agents.opencode ]
