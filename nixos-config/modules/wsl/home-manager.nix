{ config, pkgs, lib, ... }:

let
  user = "elijah";
  xdg_configHome = "/home/${user}/.config";
  shared-programs = import ../shared/home-manager.nix { inherit config pkgs lib; };
  shared-files = import ../shared/files.nix { inherit config pkgs; };
in
{
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
    # Assumes dotfiles are cloned to ~/.dotfiles
    # Adjust DOTFILES_DIR if your setup differs

    activation.linkDotfiles = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      DOTFILES_DIR="$HOME/.dotfiles"

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

      # Link kitty config (if not managed by home-manager)
      # link_config "$DOTFILES_DIR/kitty" "$HOME/.config/kitty"

      # Add more symlinks as needed:
      # link_config "$DOTFILES_DIR/some-config" "$HOME/.config/some-config"
    '';
  };

  # ============================================================================
  # PROGRAMS - Inherit shared config with WSL-specific overrides
  # ============================================================================

  # Inherit shared programs - neovim config is managed separately via symlink
  # See home.activation.linkDotfiles below
  programs = shared-programs;

  # ============================================================================
  # WSL-SPECIFIC SERVICES
  # ============================================================================

  # Note: Many desktop services from the NixOS config are not applicable in WSL
  # - No screen-locker (Windows handles this)
  # - No polybar (no X11/Wayland desktop)
  # - No dunst (Windows handles notifications)

  # TODO: Consider enabling these services for WSLg GUI apps
  # services = {
  #   # If using WSLg for GUI applications
  # };
}
