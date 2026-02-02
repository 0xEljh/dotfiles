{ config, pkgs, lib, ... }:

let
  user = "elijah";
  xdg_configHome = "/home/${user}/.config";
  shared-programs = import ../shared/home-manager.nix { inherit config pkgs lib; };
  shared-files = import ../shared/files.nix { inherit config pkgs; };
  windowsSshDir = "/mnt/c/Users/elija/.ssh";

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
      # initContent appends this to the shared config
      initContent = ''
        # WSL-specific Nix-LD and GPU library paths
        export NIX_LD_LIBRARY_PATH="/run/current-system/sw/share/nix-ld/lib"
        export NIX_LD="/run/current-system/sw/share/nix-ld/lib/ld.so"
        
        # Solves a libstdc++.so.6 error and links Windows GPU drivers
        export LD_LIBRARY_PATH="$NIX_LD_LIBRARY_PATH:/usr/lib/wsl/lib:$LD_LIBRARY_PATH"
        '';
      };
    };

  
  # systemd.user.services.python-aw-push = {
  #     Unit = {
  #         Description = "Run aw push python script with uv";
  #       };
  #       Service = {
  #           Type = "oneshot";
  #
  #           WorkingDirectory = "%h/dotfiles";
  #
  #           ExecStart = "${pkgs.bash}/bin/bash -c '${pkgs.uv}'/bin/uv run scripts/push_aw.py";
  #
  #           Environment = "PYTHONUNBUFFERED=1";
  #
  #         };
  #   };
  #
  #   systemd.user.timers.python-aw-push = {
  #       Unit = {
  #           Description = "Run push script every 11 minutes";
  #         };
  #       Timer = {
  #           OnCalendar = "*:0/11";
  #           Persistent = true;
  #         };
  #       Install = {
  #           WantedBy = ["timers.target"];
  #         };
  #     };

}
