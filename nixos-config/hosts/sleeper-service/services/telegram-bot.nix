{ user }:
{ pkgs, ... }:

let
  homeDir = "/home/${user}";
  botDir = "${homeDir}/dotfiles/scripts/personal_telegram_bot";
  envFile = "${homeDir}/.config/personal-telegram-bot/bot.env";

  botService = command: {
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    unitConfig = {
      ConditionPathExists = [
        envFile
        "${botDir}/pyproject.toml"
      ];
    };
    # systemctl/journalctl for health checks and failure context
    path = [ pkgs.systemd ];
    serviceConfig = {
      User = user;
      WorkingDirectory = botDir;
      Environment = [ "HOME=${homeDir}" ];
      EnvironmentFile = envFile;
      ExecStart = "${pkgs.uv}/bin/uv run --frozen botctl ${command}";
    };
  };

  mkOneshot = description: command:
    let base = botService command;
    in base // {
      inherit description;
      serviceConfig = base.serviceConfig // {
        Type = "oneshot";
        TimeoutStartSec = "5min";
      };
    };

  # Hooked into hosted services so a unit entering failed state pushes a
  # Telegram alert immediately, complementing the 5-minute poll.
  notifyOnFailure.onFailure = [ "personal-telegram-bot-notify-failure@%n.service" ];
in
{
  systemd.services = {
    personal-telegram-bot =
      let base = botService "run";
      in base // {
        description = "Personal Telegram bot (long-polling daemon)";
        wantedBy = [ "multi-user.target" ];
        serviceConfig = base.serviceConfig // {
          Type = "simple";
          Restart = "always";
          RestartSec = "5s";
          KillSignal = "SIGINT";
          TimeoutStopSec = "30s";
        };
      };

    personal-telegram-bot-morning =
      mkOneshot "Morning Notion Bread digest via Telegram" "send morning";

    personal-telegram-bot-health =
      mkOneshot "Service health checks, Telegram alert on transitions" "send health";

    personal-telegram-bot-hour =
      mkOneshot "ActivityWatch classification of the previous hour" "send hour";

    "personal-telegram-bot-notify-failure@" =
      mkOneshot "Telegram alert for failed unit %i" "send failure --unit %i";

    nginx = notifyOnFailure;
    kodo-api = notifyOnFailure;
    kodo-ml = notifyOnFailure;
    vamp-tutor-backend = notifyOnFailure;
    vamp-tutor-website = notifyOnFailure;
    digital-garden = notifyOnFailure;
    tea-the-gathering = notifyOnFailure;
    dotfiles-sync = notifyOnFailure;
  };

  systemd.timers = {
    personal-telegram-bot-morning = {
      description = "Morning digest at 09:30 local time";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        Unit = "personal-telegram-bot-morning.service";
        OnCalendar = "*-*-* 09:30:00";
        # Catch up after downtime; SQLite dedupe prevents double sends.
        Persistent = true;
      };
    };

    personal-telegram-bot-hour = {
      # :10 past gives device pushes (push_aw.py) time to land before classifying.
      description = "Hourly ActivityWatch classification report";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        Unit = "personal-telegram-bot-hour.service";
        OnCalendar = "*-*-* *:10:00";
      };
    };

    personal-telegram-bot-health = {
      description = "Health checks every 5 minutes";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        Unit = "personal-telegram-bot-health.service";
        OnCalendar = "*:0/5";
        RandomizedDelaySec = "30s";
      };
    };
  };
}
