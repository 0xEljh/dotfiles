{ config, pkgs, lib, ... }:

# Creates macOS aliases (not symlinks) in /Applications/Nix Apps/
# This preserves accessibility permissions across nix rebuilds since
# macOS treats aliases as stable references to the same application.
#
# Without this, every rebuild creates new store paths, and you'd need
# to re-grant accessibility permissions each time.

{
  system.activationScripts.applications.text = let
    env = pkgs.buildEnv {
      name = "system-applications";
      paths = config.environment.systemPackages;
      pathsToLink = [ "/Applications" ];
    };
  in
    lib.mkForce ''
      # Set up applications.
      echo "setting up /Applications/Nix Apps..." >&2

      rm -rf /Applications/Nix\ Apps
      mkdir -p /Applications/Nix\ Apps

      find ${env}/Applications -maxdepth 1 -type l -exec readlink '{}' + |
      while read -r src; do
        app_name=$(basename "$src")
        echo "aliasing $src -> /Applications/Nix Apps/$app_name" >&2
        ${pkgs.mkalias}/bin/mkalias "$src" "/Applications/Nix Apps/$app_name"
      done
    '';
}
