{ config, pkgs, lib, ... }:

# Accessibility Permissions for Nix-managed binaries
#
# Problem: macOS TCC (Transparency, Consent, Control) tracks Accessibility
# permissions by code signature hash (cdhash). Each Nix rebuild creates new
# store paths with different hashes, invalidating permissions.
#
# Solution: This module:
# 1. Copies binaries to stable paths (/usr/local/bin/)
# 2. Re-signs them with ad-hoc signatures and stable identifiers
# 3. Updates the yabai sudoers file with the new hash (for scripting addition)
# 4. Overrides launchd services to use stable paths
#
# After first run, grant Accessibility permissions to:
#   /usr/local/bin/yabai
#   /usr/local/bin/skhd
#
# These paths remain stable across rebuilds, so permissions persist.
#
# NOTE: kitty.app uses the mkalias approach in apps.nix since it's a .app bundle.
# Grant permissions to /Applications/Nix Apps/kitty.app

let
  user = "elijah";

  # Binaries that need Accessibility permissions
  accessibilityBinaries = [
    { name = "yabai"; pkg = pkgs.yabai; identifier = "com.koekeishiya.yabai"; }
    { name = "skhd";  pkg = pkgs.skhd;  identifier = "com.koekeishiya.skhd"; }
  ];

  # Script to copy, sign, and configure a binary
  setupAccessibilityBinary = { name, pkg, identifier }: ''
    echo "Setting up ${name} for stable Accessibility permissions..." >&2

    SRC="${pkg}/bin/${name}"
    DEST="/usr/local/bin/${name}"

    if [ ! -f "$SRC" ]; then
      echo "Warning: ${name} binary not found at $SRC" >&2
    else
      # Copy to stable location (requires sudo, run in activation)
      cp -f "$SRC" "$DEST"
      chmod 755 "$DEST"

      # Re-sign with ad-hoc signature and stable identifier
      # The identifier helps macOS recognize it as the "same" app
      codesign -fs - --identifier "${identifier}" "$DEST" 2>/dev/null || true

      echo "Installed ${name} to $DEST with identifier ${identifier}" >&2
    fi
  '';

  # Script to update yabai sudoers for scripting addition
  updateYabaiSudoers = ''
    echo "Updating yabai sudoers configuration..." >&2

    YABAI_BIN="/usr/local/bin/yabai"
    SUDOERS_FILE="/private/etc/sudoers.d/yabai"

    if [ -f "$YABAI_BIN" ]; then
      HASH=$(shasum -a 256 "$YABAI_BIN" | cut -d " " -f 1)

      # Create sudoers entry for passwordless scripting addition
      echo "${user} ALL=(root) NOPASSWD: sha256:$HASH $YABAI_BIN --load-sa" > "$SUDOERS_FILE"
      chmod 440 "$SUDOERS_FILE"
      chown root:wheel "$SUDOERS_FILE"

      echo "Updated $SUDOERS_FILE with hash $HASH" >&2
    else
      echo "Warning: yabai not found at $YABAI_BIN, skipping sudoers update" >&2
    fi
  '';

  # Yabai config content (replicating what services.yabai would generate)
  yabaiConfig = pkgs.writeText "yabairc" ''
    #!/usr/bin/env sh

    # Load scripting addition
    yabai -m signal --add event=dock_did_restart action="sudo yabai --load-sa"
    sudo yabai --load-sa

    # Configuration
    yabai -m config layout bsp
    yabai -m config window_gap 8
    yabai -m config split_ratio 0.55
    yabai -m config focus_follows_mouse autoraise
    yabai -m config mouse_follows_focus off
    yabai -m config window_opacity on
    yabai -m config active_window_opacity 0.95
    yabai -m config normal_window_opacity 0.85

    # Extra config
    # example: automatically grid-tile new floating windows
    yabai -m signal --add event=window_created \
        action="yabai -m window --grid 6:6:1:1:4:4"
    
    # Set VSCode to be transparent even when active
    yabai -m rule --add app="^Code$" opacity=0.90
  '';

in
{
  # Disable the built-in services - we'll manage them ourselves
  services.yabai.enable = lib.mkForce false;
  services.skhd.enable = lib.mkForce false;

  # Use extraActivation hook to set up accessibility binaries
  # This runs early in activation, before launchd services start
  system.activationScripts.extraActivation.text = lib.mkAfter ''
    # === Accessibility Binary Setup ===
    echo "Configuring Accessibility binaries..." >&2

    # Ensure /usr/local/bin exists
    mkdir -p /usr/local/bin

    ${lib.concatMapStringsSep "\n" setupAccessibilityBinary accessibilityBinaries}

    ${updateYabaiSudoers}

    echo "" >&2
    echo "=== Accessibility Setup Complete ===" >&2
    echo "If this is your first run, grant Accessibility permissions to:" >&2
    echo "  - /usr/local/bin/yabai" >&2
    echo "  - /usr/local/bin/skhd" >&2
    echo "  - /Applications/Nix Apps/kitty.app" >&2
    echo "" >&2
    echo "System Settings > Privacy & Security > Accessibility" >&2
    echo "These paths are stable and won't need re-granting after rebuilds." >&2
    echo "===================================" >&2
  '';

  # Custom launchd agent for yabai using stable path
  # NOTE: yabai must run as user, not root (it refuses to run as root)
  launchd.user.agents.yabai = {
    serviceConfig = {
      Label = "com.koekeishiya.yabai";
      ProgramArguments = [
        "/usr/local/bin/yabai"
        "-c"
        "${yabaiConfig}"
      ];
      EnvironmentVariables = {
        PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin";
      };
      KeepAlive = true;
      RunAtLoad = true;
      ProcessType = "Interactive";
      # Use user-specific log paths to avoid permission issues
      StandardOutPath = "/tmp/yabai_${user}.out.log";
      StandardErrorPath = "/tmp/yabai_${user}.err.log";
    };
  };

  # Custom launchd agent for skhd using stable path
  launchd.user.agents.skhd = {
    serviceConfig = {
      Label = "com.koekeishiya.skhd";
      ProgramArguments = [
        "/usr/local/bin/skhd"
        "-c"
        "/etc/skhdrc"
      ];
      EnvironmentVariables = {
        PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin";
      };
      KeepAlive = true;
      RunAtLoad = true;
      ProcessType = "Interactive";
      # Use user-specific log paths to avoid permission issues
      StandardOutPath = "/tmp/skhd_${user}.out.log";
      StandardErrorPath = "/tmp/skhd_${user}.err.log";
    };
  };

  # Write skhd config to /etc/skhdrc
  environment.etc."skhdrc".text = ''
    # focus movement (use stable path for yabai)
    alt - h : /usr/local/bin/yabai -m window --focus west
    alt - l : /usr/local/bin/yabai -m window --focus east
    alt - k : /usr/local/bin/yabai -m window --focus north
    alt - j : /usr/local/bin/yabai -m window --focus south

    # resize
    shift + alt - h : /usr/local/bin/yabai -m window --resize left:-40:0
    shift + alt - l : /usr/local/bin/yabai -m window --resize right:40:0

    # space management
    alt - return : /usr/local/bin/yabai -m window --toggle zoom-parent

    # restart yabai
    shift + alt - r : launchctl kickstart -k "gui/''${UID}/com.koekeishiya.yabai"
  '';
}
