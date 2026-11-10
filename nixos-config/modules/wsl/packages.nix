{ pkgs, fff }:

with pkgs;
let shared-packages = import ../shared/packages.nix { inherit pkgs fff; }; in
shared-packages ++ [
  gnumake
  cmake
  home-manager

  direnv

  tree
  unixtools.ifconfig
  unixtools.netstat

  inotify-tools  # inotifywait, inotifywatch - For file system events
  sqlite
  xdg-utils
]
