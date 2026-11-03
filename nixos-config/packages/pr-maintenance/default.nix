{
  lib,
  stdenvNoCC,
  python3,
  makeWrapper,
}:

stdenvNoCC.mkDerivation {
  pname = "pr-maintenance";
  version = "0.1.0";

  src = lib.cleanSourceWith {
    src = ../../../scripts/pr_maintenance/pr_maintenance;
    filter =
      path: _type:
      let
        name = baseNameOf path;
      in
      name != "__pycache__" && name != ".pytest_cache" && !(lib.hasSuffix ".pyc" name);
  };

  nativeBuildInputs = [ makeWrapper ];
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    mkdir -p "$out/lib/pr-maintenance/pr_maintenance" "$out/bin"
    cp -r . "$out/lib/pr-maintenance/pr_maintenance/"
    makeWrapper ${python3}/bin/python3 "$out/bin/pr-maintenance" \
      --set PYTHONPATH "$out/lib/pr-maintenance" \
      --set PYTHONNOUSERSITE 1 \
      --set PYTHONSAFEPATH 1 \
      --add-flags '-m pr_maintenance.cli'
    makeWrapper ${python3}/bin/python3 "$out/bin/pr-worker" \
      --set PYTHONPATH "$out/lib/pr-maintenance" \
      --set PYTHONNOUSERSITE 1 \
      --set PYTHONSAFEPATH 1 \
      --add-flags '-m pr_maintenance.worker'
    runHook postInstall
  '';

  meta = {
    description = "Opt-in PR maintenance coordinator and supervised worker";
    mainProgram = "pr-maintenance";
    platforms = lib.platforms.linux;
  };
}
