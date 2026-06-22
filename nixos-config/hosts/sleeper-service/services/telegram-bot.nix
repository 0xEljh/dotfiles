{ user }:
{ config, pkgs, ... }:

let
  homeDir = "/home/${user}";
  botDir = "${homeDir}/dotfiles/scripts/personal_telegram_bot";
  envFile = config.sops.secrets."telegram-bot.env".path;

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
      Environment = [
        "HOME=${homeDir}"
        # Non-secret config (kept out of the sops env): the Bread board URL the
        # morning digest links in its footer.
        "NOTION_BREAD_URL=https://app.notion.com/p/Get-that-bread-132300d83b7f801f9ab7c346ee3e10e6"
        # Local-hour window [floor, ceiling) the wake-triggered digest may fire
        # in; outside it the noon fallback timer covers it. Filters pre-dawn SAA
        # stirs / early alarms and late-morning nap-stops. (Mirrors the code
        # defaults; here so the window is tunable without a code edit.)
        "WAKE_GATE_HOUR=7"
        "WAKE_GATE_HOUR_END=11"
      ];
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

    # Tailnet-only HTTP endpoint for phone life events (Sleep as Android
    # webhooks, MacroDroid screen events). No firewall change: the port is not
    # publicly allowed and tailscale0 is a trusted interface; the URL token
    # gates other tailnet peers.
    personal-telegram-bot-ingest =
      let base = botService "serve-ingest";
      in base // {
        description = "Life-event ingest endpoint (sleep, screen sessions)";
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
    arxiv-mcp = notifyOnFailure;
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
      # Fallback only: the digest is normally sent the moment a wake event
      # (sleep_tracking_stopped / alarm_alert_dismiss) reaches the ingest
      # service. If no wake arrives by noon (untracked night, phone offline),
      # this sends it anyway. SQLite dedupe makes wake + this idempotent.
      description = "Morning digest noon fallback (wake-triggered is primary)";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        Unit = "personal-telegram-bot-morning.service";
        OnCalendar = "*-*-* 12:00:00";
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
