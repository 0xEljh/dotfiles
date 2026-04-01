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

 # TODO: needs review

  # Triton fallback for NixOS where /sbin/ldconfig does not exist
  TRITON_LIBCUDA_PATH = "/usr/lib/wsl/lib";

  # CUDA toolkit path
  CUDA_PATH = "${pkgs.cudatoolkit}";

  # WSL GPU library path for CUDA applications
  LD_LIBRARY_PATH = lib.mkForce "/usr/lib/wsl/lib:${pkgs.cudatoolkit}/lib:${pkgs.ncurses5}/lib";

  # Additional compiler flags for CUDA development
  EXTRA_LDFLAGS = "-L/lib -L/usr/lib/wsl/lib";
  EXTRA_CCFLAGS = "-I/usr/include";
};

}
