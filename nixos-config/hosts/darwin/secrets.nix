{ lib, ... }:

let
  # The encrypted code-signing identity (.p12). Guarded by pathExists so the
  # config still evaluates before the secret has been bootstrapped on a fresh
  # checkout — run scripts/bootstrap-codesign-secret.sh once to create it.
  codesignP12 = ../../secrets/darwin/nix-codesign.p12;
  haveCodesignP12 = builtins.pathExists codesignP12;
in
{
  # Decrypt with the Mac's SSH ed25519 host key (converted to age). The matching
  # age recipient is registered as host_darwin_macbook in ../../.sops.yaml.
  sops.age.sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];

  # The nix-codesign identity used to sign Yabai.app / Skhd.app with a stable,
  # cert-pinned designated requirement (see modules/darwin/accessibility.nix).
  # Stored as a whole-file binary secret; accessibility.nix imports it into the
  # System keychain at activation so the root-run signing step can reach it.
  sops.secrets = lib.mkIf haveCodesignP12 {
    "nix-codesign.p12" = {
      format = "binary";
      sopsFile = codesignP12;
      # Defaults (root:staff 0400) are correct — only the root activation
      # script reads it, then it is imported into the System keychain.
    };
  };
}
