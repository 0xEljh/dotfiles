{
  config,
  fff,
  lib,
  nix-index-database,
  pkgs,
  ...
}:

let
  user = "elijah";
  sharedPrograms = import ../shared/home-manager.nix { inherit config pkgs lib; };
  sharedFiles = import ../shared/files.nix { inherit config pkgs; };
  gitConfig = {
    enable = true;
    settings = {
      user.name = "0xEljh";
      user.email = "elijah@0xeljh.com";
      credential.helper = "${pkgs.gh}/bin/gh auth git-credential";
    };
  };
in
{
  imports = [
    nix-index-database.homeModules.default
    ../shared/ai-tools.nix
  ];

  home = {
    enableNixpkgsReleaseCheck = false;
    username = user;
    homeDirectory = "/home/${user}";
    stateVersion = "26.05";
    file = sharedFiles;
    packages =
      import ../shared/packages.nix { inherit pkgs fff; }
      ++ (with pkgs; [
        cmake
        gnumake
        inotify-tools
        wl-clipboard
        xdg-utils
      ]);

    activation.linkDotfiles = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      source="$HOME/dotfiles/nvim"
      destination="$HOME/.config/nvim"

      if [ -e "$source" ]; then
        if [ -e "$destination" ] || [ -L "$destination" ]; then
          rm -rf "$destination"
        fi
        mkdir -p "$(dirname "$destination")"
        ln -s "$source" "$destination"
      fi
    '';
  };

  programs = lib.recursiveUpdate sharedPrograms {
    git = gitConfig;
    nix-index-database.comma.enable = true;
  };
}
