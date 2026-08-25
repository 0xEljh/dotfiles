{
  description = "Configuration for macOS, WSL, and sleeper-service";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    home-manager.url = "github:nix-community/home-manager";
    darwin = {
      url = "github:LnL7/nix-darwin/master";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nix-homebrew = {
      url = "github:zhaofengli-wip/nix-homebrew";
    };
    homebrew-bundle = {
      url = "github:homebrew/homebrew-bundle";
      flake = false;
    };
    homebrew-core = {
      url = "github:homebrew/homebrew-core";
      flake = false;
    };
    homebrew-cask = {
      url = "github:homebrew/homebrew-cask";
      flake = false;
    };
    nixos-wsl = {
      url = "github:nix-community/NixOS-WSL/main";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    llm-agents = {
      url = "github:numtide/llm-agents.nix";
    };
    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    fff = {
      url = "github:dmtrKovalenko/fff";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # Prebuilt nix-index database, so `, <cmd>` (comma) works without the
    # hours-long local index build.
    nix-index-database = {
      url = "github:nix-community/nix-index-database";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  # Inputs consumed only via `@inputs` (specialArgs / extraSpecialArgs) look
  # unused to deadnix, but the pattern has no `...`, so every input must stay
  # listed here or evaluation fails.
  # deadnix: skip
  outputs = { self, darwin, nix-homebrew, homebrew-bundle, homebrew-core, homebrew-cask, home-manager, nixpkgs, nixos-wsl, llm-agents, sops-nix, fff, nix-index-database } @inputs:
    let
      user = "elijah";
      darwinSystems = [ "aarch64-darwin" ];
      linuxSystems = [ "x86_64-linux" ];
      devShellSystems = darwinSystems ++ linuxSystems;
      llmAgentsOverlay = _final: prev: {
        llm-agents = llm-agents.packages.${prev.stdenv.hostPlatform.system};
      };
      forDevShellSystems = f: nixpkgs.lib.genAttrs devShellSystems f;

      # Nix code-quality tools, defined once and reused by the devShell (so
      # direnv puts them on PATH in this repo) and by the `lint` app (so
      # `nix run .#lint` needs nothing installed). Deliberately repo-scoped
      # rather than added to modules/shared/packages.nix: they are only useful
      # where there is nix source to lint.
      nixQualityTools = system: with nixpkgs.legacyPackages.${system}; [
        deadnix
        nixfmt
        statix
      ];

      devShell = system: let pkgs = nixpkgs.legacyPackages.${system}; in {
        default = with pkgs; mkShell {
          nativeBuildInputs = with pkgs; [
            age
            ast-grep
            bashInteractive
            direnv
            git
            nix-direnv
            sops
            ssh-to-age
          ] ++ nixQualityTools system;
          shellHook = with pkgs; ''
            export EDITOR=vim
          '';
        };
      };
      mkApp = scriptName: system: {
        type = "app";
        program = "${(nixpkgs.legacyPackages.${system}.writeScriptBin scriptName ''
          #!/usr/bin/env bash
          PATH=${nixpkgs.legacyPackages.${system}.git}/bin:$PATH
          echo "Running ${scriptName} for ${system}"
          exec ${self}/apps/${system}/${scriptName}
        '')}/bin/${scriptName}";
      };
      mkDarwinApps = system: {
        "apply" = mkApp "apply" system;
        "build" = mkApp "build" system;
        "build-switch" = mkApp "build-switch" system;
        "copy-keys" = mkApp "copy-keys" system;
        "create-keys" = mkApp "create-keys" system;
        "check-keys" = mkApp "check-keys" system;
        "rollback" = mkApp "rollback" system;
      };

      # Hermetic app wrapper. Unlike `mkApp`, which execs a checked-in script
      # (and so needs `chmod +x` plus whatever the ambient PATH happens to
      # provide), the tool closure is pinned here — these work on a host that
      # has not rebuilt yet.
      mkShellApp = system: name: runtimeInputs: text: {
        type = "app";
        program = nixpkgs.lib.getExe (
          nixpkgs.legacyPackages.${system}.writeShellApplication {
            inherit name runtimeInputs text;
          }
        );
      };

      # NixOS hosts get the same verbs darwin already has, so `nix run
      # .#build-switch` means the same thing everywhere. nh supplies the build
      # progress view and the post-switch generation diff.
      #
      # NH_FLAKE (set by programs.nh) points at the dotfiles working tree, so
      # uncommitted edits are picked up; $PWD is the fallback when bootstrapping
      # a host whose home-manager generation does not exist yet.
      mkNixosApps = system: let pkgs = nixpkgs.legacyPackages.${system}; in {
        # --out-link into a temp dir: `nh os build` otherwise drops a `result`
        # symlink in $PWD, and `nixos-config/result` is a tracked file, so a
        # plain build would dirty the working tree every run. No `exec` here —
        # it would replace the shell and discard the cleanup trap.
        "build" = mkShellApp system "build" [ pkgs.nh pkgs.coreutils ] ''
          out="$(mktemp -d)"
          trap 'rm -rf "$out"' EXIT
          nh os build --out-link "$out/result" "''${NH_FLAKE:-$PWD}" "$@"
        '';
        "build-switch" = mkShellApp system "build-switch" [ pkgs.nh ] ''
          exec nh os switch "''${NH_FLAKE:-$PWD}" "$@"
        '';
        "rollback" = mkShellApp system "rollback" [ pkgs.nh ] ''
          exec nh os rollback "$@"
        '';
      };

      # `nix run .#lint` — statix (anti-patterns) + deadnix (dead code).
      # Formatting stays in `nix fmt` so a lint run never rewrites files.
      mkQualityApps = system: {
        "lint" = mkShellApp system "lint" (nixQualityTools system) ''
          target="''${1:-.}"
          status=0
          echo "==> statix check $target"
          statix check "$target" || status=1
          echo "==> deadnix $target"
          deadnix --fail "$target" || status=1
          exit "$status"
        '';
      };
    in
    {
      devShells = forDevShellSystems devShell;

      # `nix fmt`. nixfmt-tree (treefmt + nixfmt) rather than bare nixfmt:
      # `nixfmt --check <dir>` exits 0 even when files are unformatted, and
      # nixfmt 1.4 deprecates directory arguments outright.
      formatter = forDevShellSystems (system: nixpkgs.legacyPackages.${system}.nixfmt-tree);

      apps =
        nixpkgs.lib.genAttrs darwinSystems (system: mkDarwinApps system // mkQualityApps system)
        // nixpkgs.lib.genAttrs linuxSystems (system: mkNixosApps system // mkQualityApps system);

      darwinConfigurations = nixpkgs.lib.genAttrs darwinSystems (system: let
        user = "elijah";
        lib = nixpkgs.lib;
        pkgs = import nixpkgs {
          inherit system;
          config = {
            allowUnfree = false;
            allowBroken = false;
            allowInsecure = false;
            allowUnsupportedSystem = false;
            allowUnfreePredicate = pkg: builtins.elem (lib.getName pkg) [
              "unrar"
            ];
          };
          overlays = [
            (import ./overlays/10-yabai-fix.nix)
            (import ./overlays/20-notion-cat.nix)
            (import ./overlays/30-ty.nix)
            llmAgentsOverlay
          ];
        };
      in
        darwin.lib.darwinSystem {
          inherit system pkgs;
          specialArgs = inputs // { inherit llmAgentsOverlay; };
          modules = [
            home-manager.darwinModules.home-manager
            sops-nix.darwinModules.sops
            nix-homebrew.darwinModules.nix-homebrew
            {
              nix-homebrew = {
                inherit user;
                enable = true;
                taps = {
                  "homebrew/homebrew-core" = homebrew-core;
                  "homebrew/homebrew-cask" = homebrew-cask;
                  "homebrew/homebrew-bundle" = homebrew-bundle;
                };
                mutableTaps = false;
                autoMigrate = true;
              };
            }
            ./hosts/darwin
          ];
        }
      );

      nixosConfigurations = {
        # sleeper-service configuration
        sleeper-service = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          specialArgs = inputs // { inherit llmAgentsOverlay; };
          modules = [
	    sops-nix.nixosModules.sops
	    home-manager.nixosModules.home-manager {
	      home-manager = {
	        useGlobalPkgs = true;
		useUserPackages = true;
		extraSpecialArgs = inputs;
		backupFileExtension = "backup";
		overwriteBackup = true;
		users.${user} = import ./modules/sleeper-service/home-manager.nix;
		};
	    }
            ./hosts/sleeper-service
          ];
        };

        # NixOS on WSL inside central-node
        contents-may-differ = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          specialArgs = inputs // { inherit nixos-wsl llmAgentsOverlay; };
          modules = [
            sops-nix.nixosModules.sops
            home-manager.nixosModules.home-manager {
              home-manager = {
                useGlobalPkgs = true;
                useUserPackages = true;
                extraSpecialArgs = inputs;
                backupFileExtension = "backup";
                overwriteBackup = true;
                users.${user} = import ./modules/wsl/home-manager.nix;
              };
            }
            ./hosts/wsl
            ./modules/wsl/configuration.nix
          ];
        };
      };
  };
}
