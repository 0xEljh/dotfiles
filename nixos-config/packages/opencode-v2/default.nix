{
  lib,
  stdenv,
  fetchurl,
  autoPatchelfHook,
  makeWrapper,
  ripgrep,
}:

assert stdenv.hostPlatform.system == "x86_64-linux";

stdenv.mkDerivation (finalAttrs: {
  pname = "opencode-v2";
  version = "2.0.28";

  src = fetchurl {
    url = "https://registry.npmjs.org/@opencode/cli-linux-x64/-/cli-linux-x64-${finalAttrs.version}.tgz";
    hash = "sha256-dvhQW6r1aWXtKPMFt0xSa1TszRj+eMgWIaQJDMAmyfk=";
  };

  sourceRoot = "package";
  nativeBuildInputs = [
    autoPatchelfHook
    makeWrapper
  ];
  buildInputs = [ stdenv.cc.cc.lib ];

  dontConfigure = true;
  dontBuild = true;
  # The Bun executable includes the CLI, native addons, and web assets.
  dontStrip = true;

  installPhase = ''
    runHook preInstall

    # Preserve the complete platform distribution and its relative layout.
    mkdir -p "$out/lib/opencode-v2" "$out/bin"
    cp -r . "$out/lib/opencode-v2/"
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
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
    platforms = [ "x86_64-linux" ];
    mainProgram = "opencode";
  };
})
