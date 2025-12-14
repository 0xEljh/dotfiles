{ pkgs }:

with pkgs;
let shared-packages = import ../shared/packages.nix { inherit pkgs; }; in
shared-packages ++ [
  # ============================================================================
  # WSL-SPECIFIC PACKAGES
  # ============================================================================

  # Core development tools
  gnumake
  cmake
  home-manager

  # Development tools
  direnv

  # Text and terminal utilities
  tree
  unixtools.ifconfig
  unixtools.netstat

  # File and system utilities
  inotify-tools  # inotifywait, inotifywatch - For file system events
  sqlite
  xdg-utils

  # TODO: Consider adding these packages later
  # ============================================================================

  # GUI applications (requires WSLg)
  # google-chrome
  # firefox

  # Security and authentication
  # yubikey-agent
  # keepassxc

  # Media tools
  # vlc
  # fontconfig
  # font-manager

  # Productivity
  # bc
]
