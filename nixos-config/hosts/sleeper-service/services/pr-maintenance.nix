{ user }:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.prMaintenance;
  homeDir = "/home/${user}";
  configFile = "/etc/pr-maintenance/coordinator.json";
  json = pkgs.formats.json { };
  maintenancePackage = pkgs.callPackage ../../../packages/pr-maintenance { };
  appEnvironmentFile =
    if cfg.sopsFile != null then config.sops.secrets."pr-maintenance.env".path else cfg.environmentFile;
  webhookEnvironmentFile =
    if cfg.webhook.sopsFile != null then
      config.sops.secrets."pr-maintenance-webhook.env".path
    else
      cfg.webhook.environmentFile;

  report = pkgs.writeShellApplication {
    name = "pr-maintenance-report";
    runtimeInputs = [ pkgs.uv ];
    text = ''
      # Only the bot child loads this dotenv credential; it is not exported to
      # the coordinator, SSH transport, or coding processes.
      export PERSONAL_TELEGRAM_BOT_ENV="''${CREDENTIALS_DIRECTORY:?}/telegram-bot.env"
      cd ${lib.escapeShellArg cfg.botDirectory}
      exec uv run --frozen botctl report-plain "$@"
    '';
  };

  settings = {
    database = "/var/lib/pr-maintenance/coordinator.sqlite3";
    worker_host = "contents-may-differ";
    worker_user = user;
    worker_executable = "/run/current-system/sw/bin/pr-worker";
    worker_config = "/etc/pr-maintenance/worker.json";
    known_hosts = cfg.knownHostsFile;
    report_command = [ "${report}/bin/pr-maintenance-report" ];
    webhook_host = "127.0.0.1";
    webhook_port = cfg.webhook.port;
    webhook_path = "/pr-maintenance";
    webhook_max_body = cfg.webhook.maxBodyBytes;
  }
  // cfg.settings;

  service = {
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    onFailure = [ "personal-telegram-bot-notify-failure@%n.service" ];
    path = [
      maintenancePackage
      pkgs.openssh
      pkgs.openssl
      pkgs.gh
      pkgs.systemd
      pkgs.coreutils
    ];
    environment.HOME = homeDir;
    serviceConfig = {
      User = user;
      Group = "users";
      WorkingDirectory = "/var/lib/pr-maintenance";
      StateDirectory = "pr-maintenance";
      StateDirectoryMode = "0700";
      UMask = "0077";
      NoNewPrivileges = true;
      PrivateTmp = true;
      LoadCredential = [ "telegram-bot.env:${cfg.botEnvironmentFile}" ];
      EnvironmentFile = lib.optional (appEnvironmentFile != null) appEnvironmentFile;
      Type = "oneshot";
      TimeoutStartSec = "10min";
    };
  };
in
{
  options.services.prMaintenance = {
    enable = lib.mkEnableOption "the opt-in Eigenform PR maintenance coordinator";

    settings = lib.mkOption {
      type = lib.types.attrsOf json.type;
      default = { };
      description = "Non-secret coordinator JSON settings. Discovery and execution remain subject to the application's explicit rollout gates.";
    };

    knownHostsFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Absolute path to an independently provisioned SSH known_hosts file. No host key is fetched automatically.";
    };

    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Runtime EnvironmentFile with GITHUB_APP_ID, GITHUB_INSTALLATION_ID, and GITHUB_APP_PRIVATE_KEY_FILE. The private key itself must be separately provisioned and readable by ${user}.";
    };

    sopsFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Optional existing encrypted dotenv file for the App environment; mutually exclusive with environmentFile. No secret content belongs in settings.";
    };

    botDirectory = lib.mkOption {
      type = lib.types.str;
      default = "${homeDir}/dotfiles/scripts/personal_telegram_bot";
      description = "Existing bot checkout containing the frozen uv environment and botctl report-plain command.";
    };

    botEnvironmentFile = lib.mkOption {
      type = lib.types.str;
      default = config.sops.secrets."telegram-bot.env".path;
      defaultText = lib.literalExpression ''config.sops.secrets."telegram-bot.env".path'';
      description = "Existing bot EnvironmentFile, passed as a credential and loaded only by the private report child.";
    };

    webhook = {
      enable = lib.mkEnableOption "signed PR webhooks at hooks.0xeljh.com/pr-maintenance";

      port = lib.mkOption {
        type = lib.types.port;
        default = 8831;
        description = "Loopback webhook backend port.";
      };

      maxBodyBytes = lib.mkOption {
        type = lib.types.ints.positive;
        default = 1048576;
        description = "Maximum raw webhook body size at ingress and in the coordinator.";
      };

      environmentFile = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Separate runtime EnvironmentFile containing GITHUB_WEBHOOK_SECRET or GITHUB_WEBHOOK_SECRET_FILE; never used by tick or coding jobs.";
      };

      sopsFile = lib.mkOption {
        type = lib.types.nullOr lib.types.path;
        default = null;
        description = "Optional existing encrypted dotenv webhook-secret file; mutually exclusive with webhook.environmentFile.";
      };
    };
  };

  config = lib.mkIf cfg.enable (
    lib.mkMerge [
      {
        assertions = [
          {
            assertion = cfg.knownHostsFile != null && lib.hasPrefix "/" cfg.knownHostsFile;
            message = "prMaintenance requires an absolute, independently provisioned knownHostsFile.";
          }
          {
            assertion =
              if (settings.github_auth or "app") == "gh" then
                cfg.environmentFile == null && cfg.sopsFile == null
              else
                (cfg.environmentFile != null) != (cfg.sopsFile != null);
            message = "prMaintenance App mode requires exactly one credential environment source; personal gh mode uses host-local authentication without an App environment.";
          }
          {
            assertion = cfg.environmentFile == null || lib.hasPrefix "/" cfg.environmentFile;
            message = "prMaintenance environmentFile must be an absolute runtime path.";
          }
          {
            assertion = cfg.sopsFile == null || builtins.pathExists cfg.sopsFile;
            message = "prMaintenance sopsFile must refer to an already-provisioned encrypted dotenv file.";
          }
        ];

        environment.systemPackages = [ maintenancePackage ];
        environment.etc."pr-maintenance/coordinator.json".source =
          json.generate "pr-maintenance-coordinator.json" settings;

        systemd = {
          services = {
            pr-maintenance-inventory = lib.recursiveUpdate service {
              description = "Request a complete open-PR inventory after coordinator host startup";
              wantedBy = [ "multi-user.target" ];
              before = [ "pr-maintenance.service" ];
              serviceConfig.ExecStart = "${maintenancePackage}/bin/pr-maintenance --config ${configFile} inventory";
            };

            pr-maintenance = lib.recursiveUpdate service {
              description = "Discover and coordinate opt-in PR maintenance";
              serviceConfig.ExecStart = "${maintenancePackage}/bin/pr-maintenance --config ${configFile} tick";
            };

            # A separate unit can detect stale ticks/outbox even when dispatch fails.
            pr-maintenance-health = lib.recursiveUpdate service {
              description = "Independent PR maintenance liveness and report-outbox check";
              serviceConfig.ExecStart = "${maintenancePackage}/bin/pr-maintenance --config ${configFile} health";
            };
          };

          timers = {
            pr-maintenance = {
              description = "PR maintenance coordination every five minutes";
              wantedBy = [ "timers.target" ];
              timerConfig = {
                OnBootSec = "2min";
                OnUnitActiveSec = "5min";
                Unit = "pr-maintenance.service";
              };
            };
            pr-maintenance-health = {
              description = "Independent PR maintenance health check every five minutes";
              wantedBy = [ "timers.target" ];
              timerConfig = {
                OnBootSec = "5min";
                OnUnitActiveSec = "5min";
                Unit = "pr-maintenance-health.service";
              };
            };
          };
        };
      }

      (lib.mkIf (cfg.sopsFile != null) {
        sops.secrets."pr-maintenance.env" = {
          format = "dotenv";
          inherit (cfg) sopsFile;
          key = "";
        };
      })

      (lib.mkIf cfg.webhook.enable {
        assertions = [
          {
            assertion = (cfg.webhook.environmentFile != null) != (cfg.webhook.sopsFile != null);
            message = "PR webhooks require exactly one of webhook.environmentFile or an existing webhook.sopsFile.";
          }
          {
            assertion = cfg.webhook.environmentFile == null || lib.hasPrefix "/" cfg.webhook.environmentFile;
            message = "prMaintenance webhook.environmentFile must be an absolute runtime path.";
          }
          {
            assertion = cfg.webhook.sopsFile == null || builtins.pathExists cfg.webhook.sopsFile;
            message = "PR webhook sopsFile must refer to an already-provisioned encrypted dotenv file.";
          }
          {
            assertion =
              settings.webhook_host == "127.0.0.1"
              && settings.webhook_port == cfg.webhook.port
              && settings.webhook_path == "/pr-maintenance"
              && settings.webhook_max_body == cfg.webhook.maxBodyBytes;
            message = "PR webhook application settings must match the bounded loopback nginx route.";
          }
        ];

        systemd.services.pr-maintenance-webhook = lib.recursiveUpdate service {
          description = "Signed GitHub PR maintenance webhook receiver";
          wantedBy = [ "multi-user.target" ];
          serviceConfig = {
            Type = "simple";
            ExecStart = "${maintenancePackage}/bin/pr-maintenance --config ${configFile} serve";
            EnvironmentFile =
              lib.optional (appEnvironmentFile != null) appEnvironmentFile
              ++ lib.optional (webhookEnvironmentFile != null) webhookEnvironmentFile;
            LoadCredential = [ ];
            Restart = "on-failure";
            RestartSec = "5s";
            TasksMax = 32;
          };
        };

        services.nginx = {
          appendHttpConfig = ''
            limit_req_zone $binary_remote_addr zone=pr_maintenance:1m rate=2r/s;
            limit_conn_zone $server_name zone=pr_maintenance_connections:1m;
          '';
          virtualHosts."hooks.0xeljh.com".locations."= /pr-maintenance" = {
            proxyPass = "http://127.0.0.1:${toString cfg.webhook.port}";
            extraConfig = ''
              client_max_body_size ${toString cfg.webhook.maxBodyBytes};
              client_body_timeout 10s;
              limit_req zone=pr_maintenance burst=20 nodelay;
              limit_req_status 429;
              limit_conn pr_maintenance_connections 8;
              limit_conn_status 429;
              proxy_connect_timeout 3s;
              proxy_read_timeout 8s;
              proxy_send_timeout 8s;
            '';
          };
        };
      })

      (lib.mkIf (cfg.webhook.enable && cfg.webhook.sopsFile != null) {
        sops.secrets."pr-maintenance-webhook.env" = {
          format = "dotenv";
          sopsFile = cfg.webhook.sopsFile;
          key = "";
          restartUnits = [ "pr-maintenance-webhook.service" ];
        };
      })
    ]
  );
}
