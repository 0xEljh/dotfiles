{ lib, stdenv, fetchurl, autoPatchelfHook }:

let
  sources = {
    x86_64-linux = {
      platform = "linux-x64";
      hash = "sha256-DHXguU6dVs7Od6u30D6ZlbR7eVBt9dzKMEbuuIjbWTQ=";
    };
    aarch64-darwin = {
      platform = "darwin-aarch64";
      hash = "sha256-qd9Ibq9+nbm967H6Ql6MmAmr14OxxRjR28UJalO4Ye0=";
    };
  };
  source = sources.${stdenv.hostPlatform.system};
in
stdenv.mkDerivation {
  pname = "opencode-build-bun";
  version = "1.4.2";
  src = fetchurl {
    url = "https://registry.npmjs.org/@oven/bun-${source.platform}/-/bun-${source.platform}-1.4.2.tgz";
    inherit (source) hash;
  };
  sourceRoot = "package";
  nativeBuildInputs = lib.optional stdenv.hostPlatform.isLinux autoPatchelfHook;
  buildInputs = lib.optional stdenv.hostPlatform.isLinux stdenv.cc.cc.lib;
  dontConfigure = true;
  dontBuild = true;
  dontStrip = true;
  installPhase = ''
    runHook preInstall
    install -Dm755 bin/bun "$out/bin/bun"
    runHook postInstall
  '';
}
