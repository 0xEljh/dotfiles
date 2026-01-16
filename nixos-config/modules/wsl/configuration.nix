{ config, pkgs, lib, ... }:

{
  wsl.enable = true;
  
  wsl.useWindowsDriver = true;

  wsl.wslConf = {
    user.default = "elijah";
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
      glib
      libglvnd
      ];
    };
  
  environment.variables = {
  # We add the WSL path to the standard Nix library path
  NIX_LD_LIBRARY_PATH = lib.mkForce (with pkgs; lib.makeLibraryPath [
    stdenv.cc.cc
    zlib
    openssl
    glib
    libglvnd
  ] + ":/usr/lib/wsl/lib");

  # Use the standard path that the nix-ld module expects
  NIX_LD = lib.mkForce "/run/current-system/sw/share/nix-ld/lib/ld.so";
};

}

