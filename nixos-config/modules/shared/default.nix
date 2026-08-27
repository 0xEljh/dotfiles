{ config, pkgs, lib, llmAgentsOverlay ? null, ... }:

{

  nixpkgs = {
    config = {
      allowUnfree = false;
      allowBroken = false;
      allowInsecure = false;
      allowUnsupportedSystem = false;
      # Allow only specific unfree packages (security: explicit rather than blanket permission)
      allowUnfreePredicate = pkg: builtins.elem (lib.getName pkg) [
        "opencode-claude-agent"
        "unrar"
      ];
    };

    overlays =
      # Apply each overlay found in the /overlays directory
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
