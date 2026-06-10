{ lib, ... }: {
  # Static networking captured from the provider by nixos-infect.
  # IPv6 is unconfigured on this host; the generator emitted empty
  # defaultGateway6/ipv6.routes entries that expand to invalid
  # `ip -6 route` commands in network-setup, so they are stripped.
  # docker0 is managed by the docker daemon (random MAC per boot),
  # so it must not be declared as a static interface here.
  networking = {
    nameservers = [ "8.8.8.8" ];
    defaultGateway = "109.123.240.1";
    dhcpcd.enable = false;
    usePredictableInterfaceNames = lib.mkForce false;
    interfaces.eth0 = {
      ipv4.addresses = [
        { address = "109.123.255.31"; prefixLength = 20; }
      ];
      ipv4.routes = [ { address = "109.123.240.1"; prefixLength = 32; } ];
    };
  };
  services.udev.extraRules = ''
    ATTR{address}=="00:50:56:4b:47:77", NAME="eth0"
  '';
}
