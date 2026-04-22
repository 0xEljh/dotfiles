{ config, pkgs, lib, ... }:

# Accessibility Permissions for Nix-managed binaries
#
# Problem: macOS TCC (Transparency, Consent, Control) tracks Accessibility
# permissions by code signature. Each Nix rebuild creates new store paths
# with different binaries, invalidating ad-hoc signatures and permissions.
#
# Solution: This module:
# 1. Wraps binaries in minimal .app bundles at stable paths (/Applications/)
# 2. Signs bundles with a named certificate (DR based on cert, not cdhash)
# 3. Updates the yabai sudoers file with the new hash (for scripting addition)
# 4. Creates launchd services pointing to the .app bundle binaries
# 5. Symlinks /usr/local/bin/ for CLI convenience
#
# One-time prerequisite: Create a self-signed code signing certificate:
#   Keychain Access > Certificate Assistant > Create a Certificate...
#   Name: nix-codesign | Type: Code Signing | Identity: Self Signed Root
#
# After first run, grant Accessibility permissions to:
#   /Applications/Yabai.app
#   /Applications/Skhd.app
#
# These .app bundles + cert-based signatures persist across rebuilds.
#
# NOTE: kitty.app uses the mkalias approach in apps.nix since it's a .app bundle.
# Grant permissions to /Applications/Nix Apps/kitty.app

let
  user = "elijah";
  certName = "nix-codesign";

  capitalize = s:
    (lib.toUpper (builtins.substring 0 1 s)) +
    (builtins.substring 1 (builtins.stringLength s - 1) s);

  # Binaries that need Accessibility permissions
  accessibilityApps = [
    { name = "yabai"; pkg = pkgs.yabai; identifier = "com.koekeishiya.yabai"; }
    { name = "skhd";  pkg = pkgs.skhd;  identifier = "com.koekeishiya.skhd"; }
  ];

  # Generate a stable Info.plist for a .app bundle
  mkInfoPlist = { name, identifier, ... }: pkgs.writeText "${name}-Info.plist" ''
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
      <key>CFBundleExecutable</key>
      <string>${name}</string>
      <key>CFBundleIdentifier</key>
      <string>${identifier}</string>
      <key>CFBundleName</key>
      <string>${capitalize name}</string>
      <key>CFBundlePackageType</key>
      <string>APPL</string>
      <key>LSUIElement</key>
      <true/>
    </dict>
    </plist>
  '';

  # Script to create .app bundle, sign, and symlink a binary
  setupAccessibilityApp = { name, pkg, identifier }: let
    appName = capitalize name;
    appPath = "/Applications/${appName}.app";
    binPath = "${appPath}/Contents/MacOS/${name}";
    plist = mkInfoPlist { inherit name identifier; };
  in ''
    echo "Setting up ${appName}.app for stable Accessibility permissions..." >&2

    SRC="${pkg}/bin/${name}"
    APP_PATH="${appPath}"
    BIN_PATH="${binPath}"

    if [ ! -f "$SRC" ]; then
      echo "Warning: ${name} binary not found at $SRC" >&2
    else
      # Create .app bundle structure
      mkdir -p "$APP_PATH/Contents/MacOS"

      # Copy stable Info.plist from nix store
      cp -f "${plist}" "$APP_PATH/Contents/Info.plist"

      # Copy binary into bundle
      cp -f "$SRC" "$BIN_PATH"
      chmod 755 "$BIN_PATH"

      # Sign with named certificate; fall back to ad-hoc if cert not found
      if security find-identity -v -p codesigning 2>/dev/null | grep -q "${certName}"; then
        codesign --force --sign "${certName}" --identifier "${identifier}" "$APP_PATH" 2>/dev/null
        echo "Signed ${appName}.app with certificate '${certName}'" >&2
      else
        codesign --force -s - --identifier "${identifier}" "$APP_PATH" 2>/dev/null || true
        echo "WARNING: Certificate '${certName}' not found — signed ad-hoc (permissions will not persist across rebuilds)" >&2
        echo "  Create it: Keychain Access > Certificate Assistant > Create a Certificate" >&2
        echo "  Name: ${certName} | Identity Type: Self Signed Root | Certificate Type: Code Signing" >&2
      fi

      # Symlink to /usr/local/bin for CLI convenience (skhdrc etc.)
      ln -sf "$BIN_PATH" "/usr/local/bin/${name}"

      echo "Installed ${appName}.app → /usr/local/bin/${name}" >&2
    fi
  '';

  # Script to update yabai sudoers for scripting addition
  updateYabaiSudoers = ''
    echo "Updating yabai sudoers configuration..." >&2

    YABAI_BIN="/Applications/Yabai.app/Contents/MacOS/yabai"
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

  # Use extraActivation hook to set up accessibility .app bundles
  # This runs early in activation, before launchd services start
  system.activationScripts.extraActivation.text = lib.mkAfter ''
    # === Accessibility .app Bundle Setup ===
    echo "Configuring Accessibility .app bundles..." >&2

    # Ensure /usr/local/bin exists (for symlinks)
    mkdir -p /usr/local/bin

    ${lib.concatMapStringsSep "\n" setupAccessibilityApp accessibilityApps}

    ${updateYabaiSudoers}

    echo "" >&2
    echo "=== Accessibility Setup Complete ===" >&2
    echo "If this is your first run, grant Accessibility permissions to:" >&2
    echo "  - /Applications/Yabai.app" >&2
    echo "  - /Applications/Skhd.app" >&2
    echo "  - /Applications/Nix Apps/kitty.app" >&2
    echo "" >&2
    echo "System Settings > Privacy & Security > Accessibility" >&2
    echo "These .app bundles are stable and won't need re-granting after rebuilds." >&2
    echo "===================================" >&2
  '';

  # Custom launchd agent for yabai using .app bundle path
  # NOTE: yabai must run as user, not root (it refuses to run as root)
  launchd.user.agents.yabai = {
    serviceConfig = {
      Label = "com.koekeishiya.yabai";
      ProgramArguments = [
        "/Applications/Yabai.app/Contents/MacOS/yabai"
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

  # Custom launchd agent for skhd using .app bundle path
  launchd.user.agents.skhd = {
    serviceConfig = {
      Label = "com.koekeishiya.skhd";
      ProgramArguments = [
        "/Applications/Skhd.app/Contents/MacOS/skhd"
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
