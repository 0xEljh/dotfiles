_final: prev:

let
  version = "0.0.74";
  releases = {
    x86_64-linux = {
      target = "x86_64-unknown-linux-gnu";
      hash = "sha256-q+WEVWmFA/GA4Kqr3aVKjQoITE3sLkXv/ZAuQUZR9Lw=";
    };
    aarch64-darwin = {
      target = "aarch64-apple-darwin";
      hash = "sha256-ebCAafKYMzg2UFFaMfJgpgqBIksx/bn6IaVsHq0DKm4=";
    };
  };
  release = releases.${prev.stdenv.hostPlatform.system};
in
{
  ty = prev.stdenvNoCC.mkDerivation {
    pname = "ty";
    inherit version;

    src = prev.fetchurl {
      url = "https://github.com/astral-sh/ty/releases/download/${version}/ty-${release.target}.tar.gz";
      inherit (release) hash;
    };
    sourceRoot = "ty-${release.target}";

    dontBuild = true;
    installPhase = ''
      runHook preInstall
      install -Dm755 ty "$out/bin/ty"
      runHook postInstall
    '';

    meta = prev.ty.meta // {
      platforms = builtins.attrNames releases;
    };
  };
}
