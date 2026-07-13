{ lib, buildNpmPackage, makeWrapper, nodejs_24 }:

buildNpmPackage {
  pname = "context7-cli";
  version = "0.5.4";

  src = lib.cleanSource ./.;
  npmDepsHash = "sha256-3T9+IZOv9hC/7pwU+A1gdJ3Ytd9q9W84+pd05i6Khbg=";
  dontNpmBuild = true;

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/lib/context7-cli" "$out/bin"
    cp -r node_modules "$out/lib/context7-cli/"
    makeWrapper ${nodejs_24}/bin/node "$out/bin/ctx7" \
      --add-flags "$out/lib/context7-cli/node_modules/ctx7/dist/index.js"

    runHook postInstall
  '';

  meta = {
    description = "Context7 documentation retrieval CLI";
    homepage = "https://context7.com/docs/clients/cli";
    license = lib.licenses.mit;
    mainProgram = "ctx7";
  };
}
