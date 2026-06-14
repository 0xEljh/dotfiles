{ ... }:

{
  # Decrypt with the host's SSH ed25519 key (converted to age). This is also
  # the sops-nix default when openssh is enabled; pinned here so the secret
  # store doesn't silently depend on openssh settings.
  sops.age.sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];

  # Whole-file dotenv secrets (key = "") consumed via systemd EnvironmentFile.
  # EnvironmentFile is read before the unit drops privileges, so root-owned
  # 0400 defaults work even for User=elijah services.
  sops.secrets."telegram-bot.env" = {
    format = "dotenv";
    sopsFile = ../../secrets/sleeper-service/telegram-bot.env;
    key = "";
    restartUnits = [
      "personal-telegram-bot.service"
      "personal-telegram-bot-t3-pairing.service"
      "personal-telegram-bot-ingest.service"
    ];
  };

  sops.secrets."acme-namesilo.env" = {
    format = "dotenv";
    sopsFile = ../../secrets/sleeper-service/acme-namesilo.env;
    key = "";
    # No restartUnits: the acme oneshot runs on its own timer, and forcing a
    # run on every secret edit risks LE rate limits for no benefit.
  };

  sops.secrets."kodo-api.env" = {
    format = "dotenv";
    sopsFile = ../../secrets/sleeper-service/kodo-api.env;
    key = "";
    restartUnits = [ "kodo-api.service" ];
  };
}
