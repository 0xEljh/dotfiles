{ config, pkgs, lib, llmAgentsOverlay ? null, ... }:

{

  nixpkgs = {
    config = {
      allowUnfree = false;
      allowBroken = false;
      allowInsecure = false;
      allowUnsupportedSystem = false;
      allowUnfreePredicate = pkg: builtins.elem (lib.getName pkg) [
        "opencode-claude-agent"
        "unrar"
      ];
    };

    overlays =
      let path = ../../overlays; in with builtins;
      (map (n: import (path + ("/" + n)))
          (filter (n: match ".*\\.nix" n != null ||
                      pathExists (path + ("/" + n + "/default.nix")))
                  (attrNames (readDir path))))
      # Namespace the flake's prebuilt package outputs without rebuilding them
      # against this repository's nixpkgs.
      ++ lib.optional (llmAgentsOverlay != null) llmAgentsOverlay;
  };
}
