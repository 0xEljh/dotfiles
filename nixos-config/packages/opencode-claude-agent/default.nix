{ lib, buildNpmPackage, makeWrapper, nodejs }:

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
  npmDepsHash = "sha256-wuirBEcJKdG+TljtvBca8svgel48KSw37yD2qkicL5g=";

  npmBuildScript = "build";
  nativeBuildInputs = [ makeWrapper ];
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
    makeWrapper ${lib.getExe nodejs} "$out/lib/opencode-claude-agent/dist/connect-v2" \
      --add-flags "$out/lib/opencode-claude-agent/dist/connect-v2.js"

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
