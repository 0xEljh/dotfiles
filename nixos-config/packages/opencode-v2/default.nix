{
  lib,
  stdenv,
  fetchzip,
  callPackage,
  autoPatchelfHook,
  makeWrapper,
  ripgrep,
  nodejs,
  writableTmpDirAsHomeHook,
  idleTimeout ? "16 hours",
}:

assert stdenv.hostPlatform.system == "x86_64-linux";

let
  upstreamSource = fetchzip {
    url = "https://github.com/anomalyco/opencode/archive/026b106bc5d41b679ffc413264229c340871e4d3.tar.gz";
    hash = "sha256-7bpaROwB9JYucjqOKh92oWi2iuYmo7pXyUxYKgj4OkI=";
  };
  buildBun = callPackage ./bun-build.nix { };
  nodeModules = callPackage "${upstreamSource}/nix/node_modules.nix" {
    bun = buildBun;
    rev = "026b106";
  };
in
stdenv.mkDerivation (finalAttrs: {
  pname = "opencode-v2";
  version = "2.0.28-preserve.1";

  src = upstreamSource;
  # Upstream PR #57416 plus its Job.runningBackground prerequisite. Keep the
  # 2.x identity: T3 selects its protocol adapter from the executable's major.
  patches = [ ./preserve-active-sessions.patch ];

  nativeBuildInputs = [
    buildBun
    nodejs
    autoPatchelfHook
    makeWrapper
    writableTmpDirAsHomeHook
  ];
  buildInputs = [ stdenv.cc.cc.lib ];

  # The Bun executable includes the CLI, native addons, and web assets.
  dontStrip = true;

  postPatch = ''
    substituteInPlace packages/core/src/location-activity.ts \
      --replace-fail 'options.timeToLive ?? "60 minutes"' \
                     ${lib.escapeShellArg "options.timeToLive ?? ${builtins.toJSON idleTimeout}"}
    cp ${./location-retention.test.ts} packages/core/test/location-retention.test.ts
  '';

  configurePhase = ''
    runHook preConfigure
    cp -R ${nodeModules}/. .
    chmod -R u+w node_modules packages/*/node_modules
    patchShebangs node_modules packages/*/node_modules
    runHook postConfigure
  '';

  env = {
    LD_LIBRARY_PATH = lib.makeLibraryPath [ stdenv.cc.cc.lib ];
    LD_PRELOAD = "${stdenv.cc.cc.lib}/lib/libstdc++.so.6";
    OPENCODE_VERSION = finalAttrs.version;
    OPENCODE_CHANNEL = "latest";
    NODE_OPTIONS = "--max-old-space-size=4096";
    OPENCODE_TEST_IDLE_TTL = idleTimeout;
  };

  buildPhase = ''
    runHook preBuild
    bun run --cwd packages/cli script/build.ts --target=opencode-linux-x64 --skip-install
    runHook postBuild
  '';

  doCheck = true;
  checkPhase = ''
    runHook preCheck
    pushd packages/core
    bun test ./test/location-retention.test.ts --timeout 30000
    popd
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall

    # Preserve the complete platform distribution and its relative layout.
    mkdir -p "$out/lib/opencode-v2" "$out/bin"
    cp -r packages/cli/dist/cli-linux-x64/. "$out/lib/opencode-v2/"
    chmod +x "$out/lib/opencode-v2/bin/opencode"

    # Embedded native addons are extracted/dlopen'd at runtime, so the ELF
    # patching hook cannot give them their own Nix library search paths.
    # Preload libstdc++ for @parcel/watcher, as in the upstream Nix package.
    makeWrapper "$out/lib/opencode-v2/bin/opencode" "$out/bin/opencode" \
      --prefix PATH : ${lib.makeBinPath [ ripgrep ]} \
      --prefix LD_LIBRARY_PATH : ${lib.makeLibraryPath [ stdenv.cc.cc.lib ]} \
      --prefix LD_PRELOAD : "${stdenv.cc.cc.lib}/lib/libstdc++.so.6"

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    checkRoot=$(mktemp -d "$TMPDIR/opencode-version.XXXXXXXX")
    mkdir -p "$checkRoot"/{home,config,data,state,cache,runtime,tmp,work}
    chmod 700 "$checkRoot/runtime"
    # Clear inherited OpenCode overrides and use an empty working directory.
    # --version is the only application invocation permitted during the build.
    actualVersion=$(
      cd "$checkRoot/work"
      env -i \
        HOME="$checkRoot/home" \
        XDG_CONFIG_HOME="$checkRoot/config" \
        XDG_DATA_HOME="$checkRoot/data" \
        XDG_STATE_HOME="$checkRoot/state" \
        XDG_CACHE_HOME="$checkRoot/cache" \
        XDG_RUNTIME_DIR="$checkRoot/runtime" \
        TMPDIR="$checkRoot/tmp" \
        "$out/bin/opencode" --version
    )
    printf 'opencode --version: %s\n' "$actualVersion"
    test "$actualVersion" = "opencode v${finalAttrs.version}"
    rm -rf "$checkRoot"

    runHook postInstallCheck
  '';

  meta = {
    description = "OpenCode 2 CLI with bundled terminal interface and web assets";
    homepage = "https://opencode.ai/v2";
    license = lib.licenses.mit;
    sourceProvenance = with lib.sourceTypes; [ fromSource ];
    platforms = [ "x86_64-linux" ];
    mainProgram = "opencode";
  };
})
