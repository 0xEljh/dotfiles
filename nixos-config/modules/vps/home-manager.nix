{config, pkgs, lib, ... }:

let
	user = "elijah";
	shared-programs = import ../shared/home-manager.nix { inherit config pkgs lib; };
	shared-files = import ../shared/files.nix { inherit config pkgs; };

	git-vps-config = {
	    enable = true;
	    settings = {
	      user.name = "0xEljh";
	      user.email = "elijah@0xeljh.com";
	      credential.helper = "${pkgs.gh}/bin/gh auth git-credential";
	    };
	  };
in
{
	imports = [../shared/ai-tools.nix];
	home = {
	    username = "${user}";
	    homeDirectory = "/home/${user}";
	    packages = import ../shared/packages.nix { inherit pkgs; };
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
		git = git-vps-config;
	};

}
