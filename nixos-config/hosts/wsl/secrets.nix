{ ... }:

{
  sops.age.sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];

  sops.secrets."telegram-disk-alert.env" = {
    format = "dotenv";
    sopsFile = ../../secrets/wsl/telegram-disk-alert.env;
    key = "";
    restartUnits = [ "personal-telegram-bot-disk-health.service" ];
  };
}
