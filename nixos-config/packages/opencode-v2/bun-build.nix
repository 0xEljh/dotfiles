{ stdenv, fetchurl, autoPatchelfHook }:

stdenv.mkDerivation {
  pname = "opencode-build-bun";
  version = "1.4.2";
  src = fetchurl {
    url = "https://registry.npmjs.org/@oven/bun-linux-x64/-/bun-linux-x64-1.4.2.tgz";
    hash = "sha256-DHXguU6dVs7Od6u30D6ZlbR7eVBt9dzKMEbuuIjbWTQ=";
  };
  sourceRoot = "package";
  nativeBuildInputs = [ autoPatchelfHook ];
  buildInputs = [ stdenv.cc.cc.lib ];
  dontConfigure = true;
  dontBuild = true;
  dontStrip = true;
  installPhase = ''
    runHook preInstall
    install -Dm755 bin/bun "$out/bin/bun"
    runHook postInstall
  '';
}
