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
	];

	services.t3Serve = {
		enable = true;
		# Plain HTTP on the tailnet IP. The tailnet itself is the auth boundary;
		# we skip Tailscale HTTPS to avoid leaking device names into public CT logs.
		bindToTailscaleIp = true;
		# Upstream 0.0.28 nightly + PR #2673 (OpenCode event stream fix).
		# The old 0.0.27 patched tarball remains on disk for rollback.
		t3Package = "file:${config.home.homeDirectory}/.local/share/t3/t3-0.0.28-nightly.20260621.614-pr2673-sessionttl.0.tgz";
	};

	home = {
	    username = "${user}";
	    homeDirectory = "/home/${user}";
	    # codex is excluded on sleeper-service: building from source exhausts RAM and the
	    # numtide cache miss path is impractical here. Bring it back when there's
	    # a reliable prebuilt or more RAM.
	    packages = lib.filter
	      (p: !(lib.hasInfix "codex" (p.pname or p.name or "")))
	      (import ../shared/packages.nix { inherit pkgs fff; });
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
