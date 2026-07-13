{ lib, buildNpmPackage, makeWrapper, nodejs_24 }:

buildNpmPackage {
  pname = "playwright-cli";
  version = "0.1.17";

  src = lib.cleanSource ./.;
  npmDepsHash = "sha256-nvY6rD3/S7P4sjb9HKdsJlV4xLF4hXSoNeVlBvmS3ug=";
  dontNpmBuild = true;

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/lib/playwright-cli" "$out/bin" "$out/share/playwright-cli"
    cp -r node_modules "$out/lib/playwright-cli/"
    cp -r node_modules/@playwright/cli/skills "$out/share/playwright-cli/"
    makeWrapper ${nodejs_24}/bin/node "$out/bin/playwright-cli" \
      --add-flags "$out/lib/playwright-cli/node_modules/@playwright/cli/playwright-cli.js"

    runHook postInstall
  '';

  meta = {
    description = "Official command-line browser automation for agents";
    homepage = "https://github.com/microsoft/playwright-cli";
    license = lib.licenses.asl20;
    mainProgram = "playwright-cli";
  };
}
