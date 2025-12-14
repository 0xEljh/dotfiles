{ config, inputs, pkgs, lib, nixos-wsl, ... }:

let
  user = "elijah";
in
{
  imports = [
    nixos-wsl.nixosModules.default
    ../../modules/shared
  ];

  # ============================================================================
  # WSL-SPECIFIC CONFIGURATION
  # ============================================================================

  wsl = {
    enable = true;
    defaultUser = user;

    # WSLg - enables GUI application support via Wayland
    wslConf = {
      automount.root = "/mnt";
      interop.appendWindowsPath = true;  # Access Windows executables
      network.generateHosts = true;
    };

    # Start a Docker daemon in WSL (optional, can also use Docker Desktop)
    # docker-desktop.enable = false;
  };

  # ============================================================================
  # NIX CONFIGURATION
  # ============================================================================

  nix = {
    nixPath = [ "nixos-config=/home/${user}/.local/share/src/nixos-config:/etc/nixos" ];
    settings = {
      allowed-users = [ "${user}" ];
      trusted-users = [ "@wheel" "${user}" ];
      substituters = [ "https://nix-community.cachix.org" "https://cache.nixos.org" ];
      trusted-public-keys = [ "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY=" ];
    };

    package = pkgs.nix;
    extraOptions = ''
      experimental-features = nix-command flakes
    '';
  };

  # ============================================================================
  # PROGRAMS
  # ============================================================================

  programs = {
    gnupg.agent.enable = true;
    zsh.enable = true;

    # Neovim - available immediately as an editor
    neovim = {
      enable = true;
      defaultEditor = true;
      viAlias = true;
      vimAlias = true;
    };
  };

  # ============================================================================
  # SERVICES
  # ============================================================================

  services = {
    # SSH for remote access (useful for VS Code Remote, etc.)
    openssh = {
      enable = true;
      settings = {
        PasswordAuthentication = false;
        PermitRootLogin = "no";
      };
    };

    # TODO: Consider enabling these services later
    # syncthing = { ... };
  };

  # ============================================================================
  # VIRTUALISATION
  # ============================================================================

  virtualisation = {
    docker = {
      enable = true;
      # Use containerd for better WSL2 integration
      # TODO: Consider using podman instead for rootless containers
      # podman = { enable = true; dockerCompat = true; };
    };
  };

  # ============================================================================
  # USER CONFIGURATION
  # ============================================================================

  users.users.${user} = {
    isNormalUser = true;
    extraGroups = [
      "wheel"  # Enable 'sudo' for the user
      "docker"
    ];
    shell = pkgs.zsh;
    # TODO: Add SSH keys for authentication
    # openssh.authorizedKeys.keys = [ "ssh-ed25519 AAAA..." ];
  };

  # ============================================================================
  # SECURITY
  # ============================================================================

  security.sudo = {
    enable = true;
    extraRules = [{
      commands = [
        {
          command = "${pkgs.systemd}/bin/reboot";
          options = [ "NOPASSWD" ];
        }
      ];
      groups = [ "wheel" ];
    }];
  };

  # ============================================================================
  # SYSTEM PACKAGES
  # ============================================================================

  environment.systemPackages = with pkgs; [
    # Core utilities
    gitAndTools.gitFull
    inetutils
    curl
    wget

    # Editor
    neovim

    # TODO: Consider adding these packages later
    # wl-clipboard  # For Wayland clipboard (WSLg)
    # xclip         # For X11 clipboard fallback
  ];

  # ============================================================================
  # FONTS
  # ============================================================================

  fonts.packages = with pkgs; [
    dejavu_fonts
    jetbrains-mono
    noto-fonts
    noto-fonts-emoji
    meslo-lgs-nf
  ];

  # ============================================================================
  # ENVIRONMENT VARIABLES
  # ============================================================================

  environment.sessionVariables = {
    EDITOR = "nvim";
    VISUAL = "nvim";
  };

  # ============================================================================
  # STATE VERSION - DO NOT CHANGE
  # ============================================================================

  system.stateVersion = "24.11";
}
