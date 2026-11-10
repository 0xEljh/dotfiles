{ config, pkgs, lib, home-manager, fff, ... }:

let
  user = "elijah";
  sharedFiles = import ../shared/files.nix { inherit config pkgs; };
  additionalFiles = import ./files.nix { inherit user config pkgs; };
in
{
  imports = [
   ./dock
  ];

  users.users.${user} = {
    name = "${user}";
    home = "/Users/${user}";
    isHidden = false;
    shell = pkgs.zsh;
  };

  homebrew = {
    enable = true;
    casks = pkgs.callPackage ./casks.nix {};
    masApps = {
    };
  };

  home-manager = {
    useGlobalPkgs = true;
    users.${user} = { pkgs, config, lib, ... }:{
      imports = [ ../shared/ai-tools.nix ];

      home = {
        enableNixpkgsReleaseCheck = false;
        packages = import ./packages.nix { inherit pkgs fff; };
        file = lib.mkMerge [
          sharedFiles
          additionalFiles
          {}
        ];
        stateVersion = "23.11";
      };
      programs = {} // import ../shared/home-manager.nix { inherit config pkgs lib; };

      home.activation.generateKittenSshTmuxConf = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        src="$HOME/.config/tmux/tmux.conf"
        dest="$HOME/.config/tmux/tmux.kitten.conf"

        if [ -f "$src" ]; then
          mkdir -p "$(dirname "$dest")"
          ${pkgs.gnused}/bin/sed '/^[[:space:]]*run-shell[[:space:]]\+\/nix\/store\//d' "$src" > "$dest"
        fi
      '';

      # Marked broken Oct 20, 2022 check later to remove this
      # https://github.com/nix-community/home-manager/issues/3344
      manual.manpages.enable = false;
    };
  };

  local.dock = {
    enable = true;
    username = user;
    entries = [
      { path = "/System/Applications/System Settings.app/"; }
      {
        path = "${config.users.users.${user}.home}/Downloads";
        section = "others";
        options = "--sort name --view grid --display stack";
      }
    ];
  };

  # yabai and skhd are configured in accessibility.nix
  # This ensures stable paths at /usr/local/bin/ are used for TCC permissions

}
