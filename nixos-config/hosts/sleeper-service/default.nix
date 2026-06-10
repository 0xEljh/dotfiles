{ pkgs, lib, ... }:

let
  user = "elijah";
  sshKeys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIL+PwhQForZ4G/u3ZP1F71yiviPLPr203qOlnwVxyau5"
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIO7vyAECB207cv54kxjZbpAAeKnZSH66CNidIhLrvy1+"
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOvFKZwpaJM2I15kX/TmaZDOnfNx3LoSPsrt2XTjmk+1"
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHdQ9A08MRPfAykqUPy2aKO7NSNnixhKW3Xa7N7yTHc3"
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOk8iAnIaa1deoc7jw8YACPNVka1ZFJxhnU4G74TmS+p"
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDlOVZ9KcD3aokJ6r9ex0c1eOJX72eiQvY8eDlcQolqh"
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJhFpUfIbtvUjCO15YjsuyN9PjFLgNegURfmGoyJjEOV"
  ];
in
{
  imports = [
    ../../modules/shared
    ./services/acme.nix
    ./services/nginx-acme.nix
    (import ./services/web-apps.nix { inherit user; })
  ]
  ++ lib.optional (builtins.pathExists ./hardware-configuration.nix) ./hardware-configuration.nix
  ++ lib.optional (builtins.pathExists ./networking.nix) ./networking.nix;

  warnings = lib.optional (!(builtins.pathExists ./hardware-configuration.nix))
    "hosts/sleeper-service/hardware-configuration.nix is missing. Copy /etc/nixos/hardware-configuration.nix from the target host before building sleeper-service.";

  # Placeholder values keep flake evaluation working on non-target machines.
  # The copied hardware-configuration.nix should override these defaults.
  boot.loader.grub = {
    enable = lib.mkDefault true;
    device = lib.mkDefault "/dev/sda";
  };

  fileSystems."/" = lib.mkDefault {
    device = "/dev/disk/by-label/nixos";
    fsType = "ext4";
  };

  networking.hostName = "sleeper-service";
  networking.firewall.allowedTCPPorts = [ 19000 ];
  # Tailscale uses UDP 41641 and needs reverse-path filtering relaxed; tailscale0 is trusted.
  networking.firewall.checkReversePath = lib.mkForce "loose";
  networking.firewall.trustedInterfaces = [ "tailscale0" ];
  time.timeZone = "Asia/Singapore";

  # Tailscale daemon. Run `sudo tailscale up --ssh` once after the first build.
  services.tailscale.enable = true;

  nix = {
    nixPath = [ "nixos-config=/home/${user}/.local/share/src/nixos-config:/etc/nixos" ];
    settings = {
      allowed-users = [ user ];
      trusted-users = [ "@wheel" user ];
      substituters = [
        "https://cache.nixos.org"
        # numtide caches the llm-agents.nix builds (codex, opencode, claude-code).
        # Without this, codex builds from source and exhausts sleeper-service RAM.
        "https://numtide.cachix.org"
      ];
      trusted-public-keys = [
        "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
        "numtide.cachix.org-1:2ps1kLBUWjxIneOy1Ber+6dwNbSd05yOb6HnGfN1gvI="
      ];
    };

    package = pkgs.nix;
    extraOptions = ''
      experimental-features = nix-command flakes
    '';
  };

  programs.nix-ld = {
    enable = true;
    libraries = with pkgs; [
      stdenv.cc.cc
      zlib
      openssl
      glib
    ];
  };

  services.envfs.enable = true;

  programs = {
    gnupg.agent.enable = true;
    zsh.enable = true;

    neovim = {
	enable = true;
	defaultEditor = true;
	viAlias = true;
	vimAlias = true;
	};

  };

  services.openssh = {
    enable = true;
    openFirewall = true;
    ports = [ 22 ];
    settings = {
      PasswordAuthentication = false;
      KbdInteractiveAuthentication = false;
      PermitRootLogin = "prohibit-password";
    };
  };

  users.users = {
    ${user} = {
      isNormalUser = true;
      shell = pkgs.zsh;
      # Keep user systemd services (t3-serve) alive across logout.
      linger = true;
      extraGroups = [
        "wheel"
        "docker"
      ];
      openssh.authorizedKeys.keys = sshKeys;
    };

    root = {
      openssh.authorizedKeys.keys = sshKeys;
    };
  };

  security.sudo = {
    enable = true;
    wheelNeedsPassword = false;
  };

  virtualisation.docker = {
    enable = true;
    logDriver = "json-file";
  };

  environment.systemPackages = with pkgs; [
    curl
    git
    inetutils
    tmux
    vim
    wget
  ];

  system.stateVersion = "24.11";
}
