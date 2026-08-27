{ lib, buildNpmPackage }:

buildNpmPackage {
  pname = "opencode-claude-agent";
  version = "0.1.0";

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./package.json
      ./package-lock.json
      ./tsconfig.json
      ./src
      ./test
    ];
  };
  npmDepsHash = "sha256-fc6/q+/Y1YAE4a+1ZG/bktc7C12bQTZaIE/kaYRsAU4=";

  npmBuildScript = "build";
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    npm test
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall

    npm prune --omit=dev --ignore-scripts --offline
    mkdir -p "$out/lib/opencode-claude-agent"
    cp -r dist node_modules package.json "$out/lib/opencode-claude-agent/"

    runHook postInstall
  '';

  meta = {
    description = "Restricted Claude Agent SDK provider for OpenCode";
    license = lib.licenses.unfree;
    platforms = [
      "x86_64-linux"
      "aarch64-darwin"
    ];
  };
}
