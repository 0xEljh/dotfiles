{ config, pkgs, lib, llm-agents ? null, ... }:

{

  nixpkgs = {
    config = {
      allowUnfree = false;
      allowBroken = false;
      allowInsecure = false;
      allowUnsupportedSystem = false;
      # Allow only specific unfree packages (security: explicit rather than blanket permission)
      allowUnfreePredicate = pkg: builtins.elem (lib.getName pkg) [
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
      # Add llm-agents overlay from flake input (provides claude-code, opencode, etc.)
      ++ (if llm-agents != null then [ llm-agents.overlays.default ] else []);
  };
}
