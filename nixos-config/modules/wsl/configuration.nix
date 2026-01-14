{ config, pkgs, ... }:

{
  wsl.enable = true;

  wsl.wslConf = {
    user.default = "elijah";

    wsl2.guiApplications = true;
  };

  # Just for testing WSLg / OpenGL
  environment.systemPackages = with pkgs; [
    mesa-demos  # glxinfo
    xorg.xeyes  # test app
  ];

  programs.nix-ld = {
      enable = true;
      libraries = with pkgs; [
      stdenv.cc.cc.lib
      zlib
      openssl
      ];
    };

}

