{ lib, ... }: {
  # This file was populated at runtime with the networking
  # details gathered from the active system.
  networking = {
    nameservers = [ "8.8.8.8"
 ];
    defaultGateway = "109.123.240.1";
    defaultGateway6 = {
      address = "";
      interface = "eth0";
    };
    dhcpcd.enable = false;
    usePredictableInterfaceNames = lib.mkForce false;
    interfaces = {
      eth0 = {
        ipv4.addresses = [
          { address="109.123.255.31"; prefixLength=20; }
        ];
        ipv6.addresses = [
          
        ];
        ipv4.routes = [ { address = "109.123.240.1"; prefixLength = 32; } ];
        ipv6.routes = [ { address = ""; prefixLength = 128; } ];
      };
            docker0 = {
        ipv4.addresses = [
          { address="172.17.0.1"; prefixLength=16; }
        ];
        ipv6.addresses = [
          
        ];
        };
    };
  };
  services.udev.extraRules = ''
    ATTR{address}=="00:50:56:4b:47:77", NAME="eth0"
    ATTR{address}=="02:42:44:b8:e2:eb", NAME="docker0"
  '';
}
