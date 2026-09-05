#!/bin/bash
# Remove the plugin. Your config, the rmos state and the vault are untouched.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
id=$(jq -r '.id' "$here/manifest.json")
dest="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/$id"

if command -v omarchy >/dev/null 2>&1; then
  omarchy plugin disable "$id" >/dev/null 2>&1 || true
fi
rm -rf "$dest"
echo "Removed $dest"
echo "Kept: ~/.config/omarchy/remarkable-sync.json, your rmos config, state and vault."
echo "To revoke the tablet key too, run bin/rmos-unpair before removing this."
