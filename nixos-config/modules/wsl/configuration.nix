{ config, pkgs, lib, ... }:

{
  wsl.enable = true;
  
  wsl.useWindowsDriver = true;

  wsl.wslConf = {
    user.default = "elijah";
  };

  environment.systemPackages = with pkgs; [
    cudatoolkit
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
  NIX_LD_LIBRARY_PATH = lib.mkForce (with pkgs; lib.makeLibraryPath [
    stdenv.cc.cc
    zlib
    openssl
    glib
    libglvnd
  ] + ":/usr/lib/wsl/lib");

  # Use the standard path that the nix-ld module expects
  NIX_LD = lib.mkForce "/run/current-system/sw/share/nix-ld/lib/ld.so";

  # Triton fallback for NixOS where /sbin/ldconfig does not exist
  TRITON_LIBCUDA_PATH = "/usr/lib/wsl/lib";

  CUDA_PATH = "${pkgs.cudatoolkit}";

  LD_LIBRARY_PATH = lib.mkForce "/usr/lib/wsl/lib:${pkgs.cudatoolkit}/lib:${pkgs.ncurses5}/lib";

  EXTRA_LDFLAGS = "-L/lib -L/usr/lib/wsl/lib";
  EXTRA_CCFLAGS = "-I/usr/include";
};

}
