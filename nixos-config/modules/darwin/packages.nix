{ pkgs }:

with pkgs;
let shared-packages = import ../shared/packages.nix { inherit pkgs; }; in
shared-packages ++ [
  # packages for MacOS only

  dockutil

  yabai
  skhd

]
