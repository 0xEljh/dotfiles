{config, pkgs, lib, fff, ... }:

let
	user = "elijah";
	shared-programs = import ../shared/home-manager.nix { inherit config pkgs lib; };
	shared-files = import ../shared/files.nix { inherit config pkgs; };

	git-sleeper-service-config = {
	    enable = true;
	    settings = {
	      user.name = "0xEljh";
	      user.email = "elijah@0xeljh.com";
	      credential.helper = "${pkgs.gh}/bin/gh auth git-credential";
	    };
	  };
in
{
	imports = [
		../shared/ai-tools.nix
		../shared/t3-serve.nix
		../shared/opencode-serve.nix
	];

	services.opencodeServe = {
		enable = true;
		host = "127.0.0.1";
		port = 8779;
		useTailscaleServe = true;
		tailscaleServePort = 8779;
	};

	services.t3Serve = {
		enable = true;
		useTailscaleServe = true;
		tailscaleServePort = 443;
	};

	home = {
	    username = "${user}";
	    homeDirectory = "/home/${user}";
	    packages = import ../shared/packages.nix { inherit pkgs fff; };
	    file = shared-files;
	    stateVersion = "24.11";

	    activation.linkDotfiles = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
	      DOTFILES_DIR="$HOME/dotfiles"
	      link_config() {
		local src="$1"
		local dest="$2"
		if [ -e "$src" ]; then
		  [ -e "$dest" ] || [ -L "$dest" ] && rm -rf "$dest"
		  mkdir -p "$(dirname "$dest")"
		  ln -sf "$src" "$dest"
		fi
	      }
	      link_config "$DOTFILES_DIR/nvim" "$HOME/.config/nvim"
	    '';
	};
	
	programs = lib.recursiveUpdate shared-programs {
		git = git-sleeper-service-config;
	};

}
