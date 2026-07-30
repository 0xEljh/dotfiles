{ user }:
{ config, pkgs, ... }:

let
  homeDir = "/home/${user}";
  botDir = "${homeDir}/dotfiles/scripts/personal_telegram_bot";
in
{
  systemd.services.personal-telegram-bot-disk-health = {
    description = "Host-local disk usage alert via nervous energy";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    unitConfig.ConditionPathExists = [
      config.sops.secrets."telegram-disk-alert.env".path
      "${botDir}/pyproject.toml"
    ];
    serviceConfig = {
      Type = "oneshot";
      User = user;
      UMask = "0077";
      WorkingDirectory = botDir;
      Environment = [
        "HOME=${homeDir}"
        "BOT_STATE_DB=/var/lib/personal-telegram-bot-disk-health/state.sqlite3"
        "HEALTH_DISK_PATHS=/"
        "HEALTH_DISK_WARNING_PERCENT=80"
        "HEALTH_DISK_CRITICAL_PERCENT=90"
      ];
      EnvironmentFile = config.sops.secrets."telegram-disk-alert.env".path;
      StateDirectory = "personal-telegram-bot-disk-health";
      ExecStart = "${pkgs.uv}/bin/uv run --frozen botctl send disk-health --host contents-may-differ";
      TimeoutStartSec = "1min";
    };
  };

  systemd.timers.personal-telegram-bot-disk-health = {
    description = "Check contents-may-differ disk usage every five minutes";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "*:0/5";
      Persistent = true;
      RandomizedDelaySec = "30s";
    };
  };
}
