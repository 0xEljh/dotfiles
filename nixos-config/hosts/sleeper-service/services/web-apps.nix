{ user }:
{ config, pkgs, ... }:

let
  vampBackendPort = 18821;
  vampPostgresPort = 5433;
  vampPostgresContainerName = "vamp-tutor-postgres";
  kodoApiPort = 18002;
  kodoMLPort = 18001;
  arxivMcpPort = 18003;
  arxivMcpHost = "arxiv-mcp.0xeljh.com";
  arxivMcpStorageDir = "/var/lib/arxiv-mcp/papers";
  homeDir = "/home/${user}";
  syncScript = "${homeDir}/dotfiles/scripts/run-sync.sh";
  kodoDir = "${homeDir}/kodo-app";
  kodoApiSrc = "${kodoDir}/services/api";
  kodoMLDir = "${kodoDir}/services/ml";
  kodoConfigDir = "${homeDir}/.config/kodo";

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
    kodo-api = {
      description = "Kodo Go API";
      after = [
        "network-online.target"
        "kodo-ml.service"
      ];
      wants = [
        "network-online.target"
        "kodo-ml.service"
      ];
      wantedBy = [ "multi-user.target" ];
      unitConfig = {
        ConditionPathExists = "${kodoApiSrc}/go.mod";
      };

      serviceConfig = {
        Type = "simple";
        User = user;
        WorkingDirectory = kodoApiSrc;
        Environment = [
          "ADDR=127.0.0.1:${toString kodoApiPort}"
          "HOME=${homeDir}"
          # Force pure-Go stdlib (net, os/user) so `go run` doesn't try to invoke
          # gcc for runtime/cgo. All deps (pgx, etc.) are pure-Go already.
          "CGO_ENABLED=0"
        ];
        EnvironmentFile = config.sops.secrets."kodo-api.env".path;
        ExecStart = "${pkgs.go}/bin/go run ./cmd/api";
        Restart = "always";
        RestartSec = "5s";
        KillSignal = "SIGINT";
        KillMode = "control-group";
        TimeoutStopSec = "30s";
      };
    };

    arxiv-mcp = {
      description = "arXiv MCP server";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];

      serviceConfig = {
        Type = "simple";
        User = user;
        StateDirectory = "arxiv-mcp";
        Environment = [
          "HOME=${homeDir}"
          "TRANSPORT=streamable-http"
          "HOST=127.0.0.1"
          "PORT=${toString arxivMcpPort}"
          "ALLOWED_HOSTS=${arxivMcpHost}"
          "ALLOWED_ORIGINS=https://chatgpt.com,https://chat.openai.com,https://platform.openai.com"
          "MAX_RESULTS=50"
          "REQUEST_TIMEOUT=60"
        ];
        ExecStart = "${pkgs.uv}/bin/uvx --from arxiv-mcp-server[pdf] arxiv-mcp-server --storage-path ${arxivMcpStorageDir}";
        Restart = "always";
        RestartSec = "5s";
        KillSignal = "SIGINT";
        TimeoutStopSec = "30s";
      };
    };

    kodo-ml = {
      description = "Kodo ML API (FastAPI)";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];
      unitConfig = {
        ConditionPathExists = "${kodoMLDir}/.venv/bin/python";
      };

      serviceConfig = {
        Type = "simple";
        User = user;
        WorkingDirectory = kodoMLDir;
        EnvironmentFile = "-${kodoConfigDir}/ml.env";
        ExecStart = "${pkgs.uv}/bin/uv run --frozen uvicorn main:app --host 127.0.0.1 --port ${toString kodoMLPort}";
        Restart = "always";
        RestartSec = "5s";
        KillSignal = "SIGINT";
        TimeoutStopSec = "30s";
      };
    };

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
        Nice = 10;
        CPUWeight = 20;
        IOWeight = 20;
        IOSchedulingClass = "best-effort";
        IOSchedulingPriority = 7;
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
