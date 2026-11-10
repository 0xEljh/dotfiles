{ lib, buildNpmPackage, makeWrapper, nodejs_24, stdenv, chromium, makeFontsConf, dejavu_fonts }:

let
  fontsConf = makeFontsConf { fontDirectories = [ dejavu_fonts ]; };
in
buildNpmPackage {
  pname = "mermaid-check";
  version = "1.0.0";
  nodejs = nodejs_24;

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./cli.mjs
      ./engines.mjs
      ./package.json
      ./package-lock.json
      ./test
    ];
  };
  npmDepsHash = "sha256-+wRtpL5zVWTa6cO8nufbqSskuv+ZXRxN0hbTS4/cInw=";
  npmFlags = [ "--ignore-scripts" ];
  PUPPETEER_SKIP_DOWNLOAD = "1";
  dontNpmBuild = true;
  nativeBuildInputs = [ makeWrapper ];

  doCheck = stdenv.hostPlatform.isLinux;
  checkPhase = lib.optionalString stdenv.hostPlatform.isLinux ''
    runHook preCheck
    export HOME="$TMPDIR/home"
    mkdir -p "$HOME"
    export MERMAID_CHECK_BROWSER=${chromium}/bin/chromium
    export FONTCONFIG_FILE=${fontsConf}
    npm test
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p "$out/lib/mermaid-check" "$out/bin"
    cp -r node_modules cli.mjs engines.mjs package.json "$out/lib/mermaid-check/"
    makeWrapper ${nodejs_24}/bin/node "$out/bin/mermaid-check" \
      --add-flags "$out/lib/mermaid-check/cli.mjs" \
      ${lib.optionalString stdenv.hostPlatform.isLinux ''
        --set-default MERMAID_CHECK_BROWSER ${chromium}/bin/chromium \
        --set-default FONTCONFIG_FILE ${fontsConf}
      ''}
    runHook postInstall
  '';

  meta = {
    description = "Verify Mermaid diagrams with full rendering, syntax-only, or Pele checks";
    license = lib.licenses.mit;
    mainProgram = "mermaid-check";
    platforms = lib.platforms.linux ++ lib.platforms.darwin;
  };
}
