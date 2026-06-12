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
      inputs.nixpkgs.follows = "nixpkgs";
    };
    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, darwin, nix-homebrew, homebrew-bundle, homebrew-core, homebrew-cask, home-manager, nixpkgs, nixos-wsl, llm-agents, sops-nix } @inputs:
    let
      user = "elijah";
      darwinSystems = [ "aarch64-darwin" "x86_64-darwin" ];
      devShellSystems = darwinSystems ++ [ "x86_64-linux" ];
      forDevShellSystems = f: nixpkgs.lib.genAttrs devShellSystems f;
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
          ];
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
    in
    {
      devShells = forDevShellSystems devShell;
      apps = nixpkgs.lib.genAttrs darwinSystems mkDarwinApps;

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
            (import ./overlays/20-notion-cat.nix)
          ] ++ (if llm-agents != null then [ llm-agents.overlays.default ] else []);
        };
      in
        darwin.lib.darwinSystem {
          inherit system pkgs;
          specialArgs = inputs;
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
          specialArgs = inputs;
          modules = [
	    sops-nix.nixosModules.sops
	    home-manager.nixosModules.home-manager {
	      home-manager = {
	        useGlobalPkgs = true;
		useUserPackages = true;
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
          specialArgs = inputs // { inherit nixos-wsl; };
          modules = [
            sops-nix.nixosModules.sops
            home-manager.nixosModules.home-manager {
              home-manager = {
                useGlobalPkgs = true;
                useUserPackages = true;
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
