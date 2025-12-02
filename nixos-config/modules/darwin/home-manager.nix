{ config, pkgs, lib, home-manager, ... }:

let
  user = "elijah";
  # Define the content of your file as a derivation
  sharedFiles = import ../shared/files.nix { inherit config pkgs; };
  additionalFiles = import ./files.nix { inherit user config pkgs; };
in
{
  imports = [
   ./dock
  ];

  # It me
  users.users.${user} = {
    name = "${user}";
    home = "/Users/${user}";
    isHidden = false;
    shell = pkgs.zsh;
  };

  homebrew = {
    enable = true;
    casks = pkgs.callPackage ./casks.nix {};
    # onActivation.cleanup = "uninstall";

    # These app IDs are from using the mas CLI app
    # mas = mac app store
    # https://github.com/mas-cli/mas
    #
    # $ nix shell nixpkgs#mas
    # $ mas search <app name>
    #
    # If you have previously added these apps to your Mac App Store profile (but not installed them on this system),
    # you may receive an error message "Redownload Unavailable with This Apple ID".
    # This message is safe to ignore. (https://github.com/dustinlyons/nixos-config/issues/83)
    masApps = {
      # "wireguard" = 1451685025;
    };
  };

  # Enable home-manager
  home-manager = {
    useGlobalPkgs = true;
    users.${user} = { pkgs, config, lib, ... }:{
      home = {
        enableNixpkgsReleaseCheck = false;
        packages = pkgs.callPackage ./packages.nix {};
        file = lib.mkMerge [
          sharedFiles
          additionalFiles
          {}
        ];
        stateVersion = "23.11";
      };
      programs = {} // import ../shared/home-manager.nix { inherit config pkgs lib; };

      # Marked broken Oct 20, 2022 check later to remove this
      # https://github.com/nix-community/home-manager/issues/3344
      manual.manpages.enable = false;
    };
  };

  # Fully declarative dock using the latest from Nix Store
  # NOTE: change this up!
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

  services.yabai = {
    enable = true;
    enableScriptingAddition = true;        # requires partial-SIP
    config = {
      layout = "bsp";
      window_gap          = 8;
      split_ratio         = 0.55;
      focus_follows_mouse = "autoraise";
      mouse_follows_focus = "off";

      window_opacity      = true;        # Enable the opacity feature
      active_window_opacity = 0.95;       # Almost opaque when focused
      normal_window_opacity = 0.85;      # Slightly transparent when unfocused

    };
    extraConfig = ''
      # example: automatically grid-tile new floating windows
      yabai -m signal --add event=window_created \
          action="yabai -m window --grid 6:6:1:1:4:4"
      
      # Set VSCode to be transparent even when active
      yabai -m rule --add app="^Code$" opacity=0.90

    '';
  };

  services.skhd = {
      enable = true;
      skhdConfig = ''
        # focus movement
        alt - h : yabai -m window --focus west
        alt - l : yabai -m window --focus east
        alt - k : yabai -m window --focus north
        alt - j : yabai -m window --focus south

        # resize
        shift + alt - h : yabai -m window --resize left:-40:0
        shift + alt - l : yabai -m window --resize right:40:0

        # space management
        alt - return : yabai -m window --toggle zoom-parent
      '';
    };

}
