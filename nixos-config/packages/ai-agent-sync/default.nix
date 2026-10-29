{ pkgs }:
let
  python = pkgs.python3.withPackages (ps: [
    ps.pyyaml
    ps.tomli-w
  ]);
in
pkgs.writeShellApplication {
  name = "ai-agents-sync";
  runtimeInputs = [
    python
    pkgs.watchexec
  ];
  text = ''
    exec python3 ${./sync_agents.py} "$@"
  '';
}
