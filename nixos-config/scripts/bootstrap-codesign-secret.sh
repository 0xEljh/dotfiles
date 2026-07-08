#!/bin/sh -e
# One-time bootstrap for Approach B: capture the local `nix-codesign` identity
# (cert + private key) from the login keychain and store it as a sops-encrypted
# binary secret at secrets/darwin/nix-codesign.p12. After this, every
# `build-switch` imports it into the System keychain automatically (see
# modules/darwin/accessibility.nix) and the yabai/skhd Accessibility grant
# persists across rebuilds and across machines that hold a registered key.
#
# Prereqs:
#   - The nix-codesign identity already exists in your login keychain.
#   - The Mac's age recipient is registered in nixos-config/.sops.yaml
#     (host_darwin_macbook) — already done.
#   - `sops` on PATH (it is, via modules/shared/packages.nix).
#
# Run from the repo:  sh nixos-config/scripts/bootstrap-codesign-secret.sh

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)   # -> nixos-config/
OUT="$REPO_DIR/secrets/darwin/nix-codesign.p12"
TMP=/tmp/nix-codesign-bootstrap.p12

if ! security find-identity -p codesigning | grep -q nix-codesign; then
  echo "ERROR: no 'nix-codesign' identity in the login keychain. Create it first:" >&2
  echo "  Keychain Access > Certificate Assistant > Create a Certificate" >&2
  echo "  Name: nix-codesign | Self Signed Root | Code Signing" >&2
  exit 1
fi

echo ">> Exporting nix-codesign identity (you'll be prompted to allow access)..."
security export -k "$HOME/Library/Keychains/login.keychain-db" \
  -t identities -f pkcs12 -P x -o "$TMP"

echo ">> Encrypting into $OUT via sops (recipients from .sops.yaml)..."
mkdir -p "$REPO_DIR/secrets/darwin"
( cd "$REPO_DIR" && sops --encrypt --input-type binary --output-type binary "$TMP" ) > "$OUT"
rm -f "$TMP"

echo ">> Done. Verify it decrypts, then commit:"
echo "   ( cd $REPO_DIR && sops --decrypt --input-type binary --output-type binary secrets/darwin/nix-codesign.p12 | file - )"
echo "   git add nixos-config/secrets/darwin/nix-codesign.p12 nixos-config/.sops.yaml"
echo ""
echo ">> Then run build-switch. On THIS machine the identity is already in the"
echo "   System keychain (from fix-yabai-perms.sh), so the import no-ops; on a"
echo "   fresh machine the first switch installs the secret and the second"
echo "   switch imports + signs with it."
