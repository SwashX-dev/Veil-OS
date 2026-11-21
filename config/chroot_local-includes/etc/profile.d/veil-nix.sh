# shellcheck shell=sh
# User-installed Nix packages (veil install ...)
if [ -d "$HOME/.local/state/nix/profile/bin" ]; then
    case ":$PATH:" in *":$HOME/.local/state/nix/profile/bin:"*) ;; *) PATH="$HOME/.local/state/nix/profile/bin:$PATH" ;; esac
fi
export NIX_REMOTE=""
