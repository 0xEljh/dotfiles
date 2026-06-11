{ lib, ... }: {
  # IPv4-only for sleeper-service. Do NOT re-add empty IPv6 fields — newer nixpkgs
  # inlines the default-gateway add into network-addresses-eth0.service, and
  # an empty `ipv6.routes` entry (e.g. `{ address = ""; prefixLength = 128; }`)
  # makes the script bail under `set -e` before the default gateway is added,
  # killing all outbound connectivity (SSH, Tailscale, ACME). 2026-06-10.
  networking = {
    nameservers = [ "8.8.8.8" ];
    defaultGateway = "109.123.240.1";
    dhcpcd.enable = false;
    usePredictableInterfaceNames = lib.mkForce false;
    interfaces = {
      eth0 = {
        ipv4.addresses = [
          { address = "109.123.255.31"; prefixLength = 20; }
        ];
        ipv4.routes = [ { address = "109.123.240.1"; prefixLength = 32; } ];
      };
      docker0 = {
        ipv4.addresses = [
          { address = "172.17.0.1"; prefixLength = 16; }
        ];
      };
    };
  };
  services.udev.extraRules = ''
    ATTR{address}=="00:50:56:4b:47:77", NAME="eth0"
    ATTR{address}=="02:42:44:b8:e2:eb", NAME="docker0"
  '';
}
