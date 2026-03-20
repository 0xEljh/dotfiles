{ user }:
{ pkgs, ... }:

let
  vampBackendPort = 18821;
  vampPostgresPort = 5433;
  vampPostgresContainerName = "vamp-tutor-postgres";
  homeDir = "/home/${user}";
  syncScript = "${homeDir}/dotfiles/scripts/run-sync.sh";

  mkNextApp = {
    name,
    port,
    workingDirectory,
    extraEnvironment ? [ ],
  }: {
    description = "Next.js app: ${name}";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    unitConfig = {
      ConditionPathExists = "${workingDirectory}/.next/BUILD_ID";
    };

    serviceConfig = {
      Type = "simple";
      User = user;
      WorkingDirectory = workingDirectory;
      Environment = [
        "NODE_ENV=production"
        "HOSTNAME=127.0.0.1"
        "PORT=${toString port}"
      ] ++ extraEnvironment;
      ExecStart = "${pkgs.bun}/bin/bun run start";
      Restart = "always";
      RestartSec = "5s";
      KillSignal = "SIGINT";
      TimeoutStopSec = "30s";
    };
  };
in
{
  virtualisation.oci-containers = {
    backend = "docker";
    containers.${vampPostgresContainerName} = {
      image = "pgvector/pgvector:pg16";
      autoStart = true;
      environment = {
        POSTGRES_USER = "postgres";
        POSTGRES_PASSWORD = "postgres";
        POSTGRES_DB = "carddb";
      };
      ports = [ "127.0.0.1:${toString vampPostgresPort}:5432" ];
      volumes = [ "vamp-tutor-pgdata:/var/lib/postgresql/data" ];
    };
  };

  systemd.services = {
    digital-garden = mkNextApp {
      name = "digital-garden";
      port = 3005;
      workingDirectory = "${homeDir}/digital-garden";
    };

    tea-the-gathering = mkNextApp {
      name = "tea-the-gathering";
      port = 3006;
      workingDirectory = "${homeDir}/tea-the-gathering";
    };

    vamp-tutor-website = mkNextApp {
      name = "vamp-tutor-website";
      port = 3007;
      workingDirectory = "${homeDir}/vamp-tutor-website";
      extraEnvironment = [
        "BACKEND_URL=http://127.0.0.1:${toString vampBackendPort}"
      ];
    };

    vamp-tutor-backend = {
      description = "FastAPI app: vamp-tutor-backend";
      after = [
        "network-online.target"
        "docker-${vampPostgresContainerName}.service"
      ];
      requires = [ "docker-${vampPostgresContainerName}.service" ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];
      unitConfig = {
        ConditionPathExists = "${homeDir}/vamp-tutor-website/backend/.venv/bin/python";
      };

      serviceConfig = {
        Type = "simple";
        User = user;
        WorkingDirectory = "${homeDir}/vamp-tutor-website/backend";
        Environment = [
          "DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:${toString vampPostgresPort}/carddb"
        ];
        ExecStartPre = "${pkgs.bash}/bin/bash -c 'for i in {1..60}; do ${pkgs.postgresql}/bin/pg_isready --quiet -h 127.0.0.1 -p ${toString vampPostgresPort} && exit 0; sleep 1; done; exit 1'";
        ExecStart = "${pkgs.uv}/bin/uv run --frozen uvicorn vamp_tutor.main:app --host 127.0.0.1 --port ${toString vampBackendPort}";
        Restart = "always";
        RestartSec = "5s";
        KillSignal = "SIGINT";
        TimeoutStopSec = "30s";
      };
    };

    dotfiles-sync = {
      description = "Run dotfiles sync script";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      unitConfig = {
        ConditionPathExists = syncScript;
      };
      path = with pkgs; [
        bash
        coreutils
        curl
        findutils
        gnugrep
        gnused
        uv
      ];

      serviceConfig = {
        Type = "oneshot";
        User = user;
        WorkingDirectory = "${homeDir}/dotfiles/scripts";
        ExecStart = "${pkgs.bash}/bin/bash ${syncScript}";
        TimeoutStartSec = "30min";
      };
    };
  };

  systemd.timers.dotfiles-sync = {
    description = "Run dotfiles sync every 20 minutes";
    wantedBy = [ "timers.target" ];

    timerConfig = {
      Unit = "dotfiles-sync.service";
      OnCalendar = "*:0/20";
      Persistent = true;
      RandomizedDelaySec = "2m";
    };
  };
}
