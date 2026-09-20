{ writeShellApplication, coreutils, bash, opencode }:

writeShellApplication {
  name = "opencode";
  runtimeInputs = [ coreutils bash ];
  text = ''
    exec bash ${./buffered-discovery.sh} ${opencode}/bin/opencode "$@"
  '';
}
