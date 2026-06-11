{config, pkgs, lib, ... }:

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
	];

	services.t3Serve = {
		enable = true;
		# Plain HTTP on the tailnet IP. The tailnet itself is the auth boundary;
		# we skip Tailscale HTTPS to avoid leaking device names into public CT logs.
		bindToTailscaleIp = true;
		# Upstream 0.0.27 + open PRs #2673 (OpenCode event stream fix) and
		# #2811 (OpenCode skills, parser hardened). Rebuild recipe and combined
		# diff: docs/patches/t3-0.0.27-pr2673-2811.0.patch + design doc.
		t3Package = "file:/home/elijah/.local/share/t3/t3-0.0.27-pr2673-2811.0.tgz";
	};

	home = {
	    username = "${user}";
	    homeDirectory = "/home/${user}";
	    # codex is excluded on sleeper-service: building from source exhausts RAM and the
	    # numtide cache miss path is impractical here. Bring it back when there's
	    # a reliable prebuilt or more RAM.
	    packages = lib.filter
	      (p: !(lib.hasInfix "codex" (p.pname or p.name or "")))
	      (import ../shared/packages.nix { inherit pkgs; });
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
