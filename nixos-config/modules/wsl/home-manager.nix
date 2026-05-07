{ config, pkgs, lib, ... }:

let
  user = "elijah";
  xdg_configHome = "/home/${user}/.config";
  shared-programs = import ../shared/home-manager.nix { inherit config pkgs lib; };
  shared-files = import ../shared/files.nix { inherit config pkgs; };
  windowsSshDir = "/mnt/c/Users/elija/.ssh";
  awPushPath = lib.makeBinPath [
    pkgs.coreutils
    pkgs.curl
    pkgs.openssh
    pkgs.rsync
    pkgs.uv
  ];
  openportalPath = lib.makeBinPath ([
    pkgs.bun
    pkgs.nodejs_24
    pkgs.git
    pkgs.coreutils
    pkgs.bash
  ] ++ lib.optionals (pkgs ? llm-agents && pkgs.llm-agents ? opencode) [
    pkgs.llm-agents.opencode
  ]);

  git-wsl-config = {
    enable = true;
    settings = {
      user.name = "0xEljh";
      user.email = "elijah@0xeljh.com";
      credential.helper = "${pkgs.gh}/bin/gh auth git-credential";
      # Force HTTPS instead of SSH
      url."https://github.com/".insteadOf = "git@github.com:";
    };
  };
in
{
  imports = [ ../shared/ai-tools.nix ];
  home = {
    enableNixpkgsReleaseCheck = false;
    username = "${user}";
    homeDirectory = "/home/${user}";
    packages = import ./packages.nix { inherit pkgs; };
    file = shared-files;
    stateVersion = "24.11";

    # ============================================================================
    # DOTFILES SYMLINK AUTOMATION
    # ============================================================================
    # This activation script creates symlinks for configs managed outside of Nix
    # (e.g., nvim config that you want to edit directly without rebuilding)
    #
    # Assumes dotfiles are cloned to ~/dotfiles
    # Adjust DOTFILES_DIR if your setup differs

    activation.linkDotfiles = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      DOTFILES_DIR="$HOME/dotfiles"

      # Helper function to create symlink (removes existing if present)
      link_config() {
        local src="$1"
        local dest="$2"

        if [ -e "$src" ]; then
          # Remove existing file/symlink/directory at destination
          if [ -e "$dest" ] || [ -L "$dest" ]; then
            rm -rf "$dest"
          fi

          # Ensure parent directory exists
          mkdir -p "$(dirname "$dest")"

          # Create symlink
          ln -sf "$src" "$dest"
          echo "Linked: $src -> $dest"
        else
          echo "Warning: Source not found, skipping: $src"
        fi
      }

      # Link nvim config
      link_config "$DOTFILES_DIR/nvim" "$HOME/.config/nvim"
    '';
  };

  # ============================================================================
  # PROGRAMS - Inherit shared config with WSL-specific overrides
  # ============================================================================

  # Inherit shared programs - neovim config is managed separately via symlink
  programs = lib.recursiveUpdate shared-programs {
    git = git-wsl-config;
    ssh = {
      includes = [
        "${windowsSshDir}/config"
      ];
      matchBlocks = {
        "*" = {
          identityFile = [
            "${windowsSshDir}/id_ed25519"
          ];
        };
      };
    };
    zsh = {
      initContent = shared-programs.zsh.initContent + ''
        # WSL-specific Nix-LD and GPU library paths
        export NIX_LD_LIBRARY_PATH="/run/current-system/sw/share/nix-ld/lib"
        export NIX_LD="/run/current-system/sw/share/nix-ld/lib/ld.so"
        export TRITON_LIBCUDA_PATH="/usr/lib/wsl/lib"
        
        # Solves a libstdc++.so.6 error and links Windows GPU drivers
        export LD_LIBRARY_PATH="$NIX_LD_LIBRARY_PATH:/usr/lib/wsl/lib:$LD_LIBRARY_PATH"
        
        # for running kitty via WSLg
        alias kitty='MESA_LOADER_DRIVER_OVERRIDE=d3d12 kitty --detach 2>/dev/null'
        '';
      };
    };

  systemd.user.services.aw-push = {
    Unit = {
      Description = "Push ActivityWatch data to VPS";
    };
    Service = {
      Type = "oneshot";
      WorkingDirectory = "%h/dotfiles";
      ExecStart = pkgs.writeShellScript "aw-push" ''
        if ! ${pkgs.curl}/bin/curl -fsS http://localhost:5600/api/0/buckets >/dev/null 2>&1; then
          exit 0
        fi

        ${pkgs.uv}/bin/uv run scripts/push_aw.py
      '';
      Environment = [
        "PYTHONUNBUFFERED=1"
        "PATH=${awPushPath}"
      ];
      TimeoutStartSec = "15min";
    };
  };

  systemd.user.timers.aw-push = {
    Unit = {
      Description = "Push ActivityWatch data hourly";
    };
    Timer = {
      OnCalendar = "hourly";
      Persistent = true;
      RandomizedDelaySec = "5m";
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };

  # OpenPortal: mobile-first web UI for opencode, reachable over Tailscale
  # at http://<tailscale-host>:8765. Spawns its own opencode server on :4765.
  # Inherits ~/.config/opencode/opencode.json (linked by ai-tools.nix).
  systemd.user.services.openportal = {
    Unit = {
      Description = "OpenPortal mobile web UI for opencode";
      After = [ "network-online.target" ];
      Wants = [ "network-online.target" ];
    };
    Service = {
      Type = "simple";
      WorkingDirectory = "%h";
      ExecStart = "${pkgs.bun}/bin/bunx openportal -d %h --hostname 0.0.0.0 --port 8765 --opencode-port 4765";
      Restart = "on-failure";
      RestartSec = "10s";
      # First run downloads openportal via bunx; allow time on slow links.
      TimeoutStartSec = "5min";
      Environment = [
        "PATH=${openportalPath}"
        "HOME=%h"
      ];
    };
    Install = {
      WantedBy = [ "default.target" ];
    };
  };
}
