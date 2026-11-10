{
  config,
  lib,
  pkgs,
  ...
}:

let
  user = "elijah";
  sshKeys = [
    # Current workstation key.
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIL+PwhQForZ4G/u3ZP1F71yiviPLPr203qOlnwVxyau5"
    # Existing contents-may-differ access keys.
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDlOVZ9KcD3aokJ6r9ex0c1eOJX72eiQvY8eDlcQolqh"
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJhFpUfIbtvUjCO15YjsuyN9PjFLgNegURfmGoyJjEOV"
  ];
in
{
  imports = [
    ../../modules/shared
    ./hardware-configuration.nix
  ];

  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  networking = {
    hostName = "new-pc";
    networkmanager.enable = true;
    firewall = {
      enable = true;
      checkReversePath = "loose";
      trustedInterfaces = [ "tailscale0" ];
      allowedUDPPorts = [ 41641 ];
    };
  };

  time.timeZone = "Asia/Singapore";
  i18n.defaultLocale = "en_US.UTF-8";

  # NVIDIA's driver and CUDA packages are unfree. Avoid global cudaSupport:
  # projects can select their own framework and CUDA package set.
  nixpkgs.config.allowUnfree = lib.mkForce true;

  nix = {
    settings = {
      experimental-features = [
        "nix-command"
        "flakes"
      ];
      allowed-users = [ user ];
      trusted-users = [
        "@wheel"
        user
      ];
      substituters = [
        "https://cache.nixos.org"
        "https://nix-community.cachix.org"
        "https://cache.numtide.com"
        "https://cache.nixos-cuda.org"
      ];
      trusted-public-keys = [
        "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
        "nix-community.cachix.org-1:mB9FSh9qf2dCimDSUo8Yrg+bU/s5/f/y/K5PCI4oaLY="
        "niks3.numtide.com-1:DTx8wZduET09hRmMtKdQDxNNthLQETkc/yaX7M4qK0g="
        "cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M="
      ];
    };

    gc = {
      automatic = true;
      dates = "Sun *-*-* 03:00:00";
      options = "--delete-older-than 30d";
      persistent = true;
      randomizedDelaySec = "15min";
    };

    optimise = {
      automatic = true;
      dates = "Mon *-*-* 03:00:00";
      persistent = true;
      randomizedDelaySec = "15min";
    };
  };

  users.users.${user} = {
    isNormalUser = true;
    description = "Elijah";
    linger = true;
    shell = pkgs.zsh;
    extraGroups = [
      "wheel"
      "networkmanager"
      "video"
      "render"
      "docker"
    ];
    openssh.authorizedKeys.keys = sshKeys;
  };

  programs = {
    gnupg.agent.enable = true;
    zsh.enable = true;
    neovim = {
      enable = true;
      defaultEditor = true;
      viAlias = true;
      vimAlias = true;
    };
    nix-ld = {
      enable = true;
      libraries = with pkgs; [
        stdenv.cc.cc
        zlib
        openssl
        glib
        libglvnd
      ];
    };
  };

  services = {
    openssh = {
      enable = true;
      openFirewall = true;
      settings = {
        PermitRootLogin = "no";
        PasswordAuthentication = false;
        KbdInteractiveAuthentication = false;
      };
    };

    tailscale = {
      enable = true;
      openFirewall = true;
      extraSetFlags = [
        "--operator=${user}"
        "--accept-dns=false"
      ];
    };

    xserver = {
      enable = true;
      videoDrivers = [ "nvidia" ];
      xkb.layout = "us";
    };
    displayManager.gdm.enable = true;
    desktopManager.gnome.enable = true;

    pipewire = {
      enable = true;
      alsa.enable = true;
      alsa.support32Bit = true;
      pulse.enable = true;
    };

    envfs.enable = true;
    fstrim.enable = true;
  };

  security = {
    rtkit.enable = true;
    # Key-only SSH remains remotely recoverable without a mutable account password.
    sudo.wheelNeedsPassword = false;
  };
  hardware = {
    enableRedistributableFirmware = true;
    graphics.enable = true;
    nvidia = {
      open = true;
      modesetting.enable = true;
      package = config.boot.kernelPackages.nvidiaPackages.stable;
    };
    nvidia-container-toolkit.enable = true;
  };

  zramSwap.enable = true;

  virtualisation.docker = {
    enable = true;
    daemon.settings = {
      features.cdi = true;
      default-address-pools = [
        {
          base = "172.17.0.0/12";
          size = 24;
        }
      ];
    };
    autoPrune = {
      enable = true;
      dates = "Wed *-*-* 03:00:00";
      flags = [ "--filter=until=168h" ];
      persistent = true;
      randomizedDelaySec = "15min";
    };
  };

  environment = {
    systemPackages = with pkgs; [
      cudaPackages.cudatoolkit
      curl
      firefox
      git
      inetutils
      kitty
      nvidia-container-toolkit
      nvtopPackages.nvidia
      pciutils
      tmux
      wget
    ];
    sessionVariables = {
      CUDA_PATH = "${pkgs.cudaPackages.cudatoolkit}";
      EDITOR = "nvim";
      VISUAL = "nvim";
    };
  };

  fonts.packages = with pkgs; [
    dejavu_fonts
    noto-fonts
    noto-fonts-color-emoji
    meslo-lgs-nf
  ];

  system.stateVersion = "26.05";
}
