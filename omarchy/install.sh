#!/bin/bash
# Install this plugin into Omarchy for local development.
#
# Publishing it properly means `omarchy plugin add <git-url>`, which clones a
# repo and expects manifest.json at its root - so that needs this directory to
# be its own repository. Until then, this copies (or links) it into place.
set -euo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
id=$(jq -r '.id' "$here/manifest.json")
plugins="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins"
dest="$plugins/$id"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy"
config="$config_dir/remarkable-sync.json"

link=0
[[ ${1:-} == --link ]] && link=1

if command -v omarchy >/dev/null 2>&1; then
  omarchy plugin validate "$here" >/dev/null || {
    echo "manifest did not validate; refusing to install" >&2
    exit 1
  }
fi

mkdir -p "$plugins"
if [[ -e $dest || -L $dest ]]; then
  rm -rf "$dest"
fi

if (( link )); then
  ln -s "$here" "$dest"
  echo "Linked $dest -> $here"
else
  cp -r "$here" "$dest"
  echo "Copied to $dest"
fi

if [[ ! -f $config ]]; then
  mkdir -p "$config_dir"
  cp "$here/remarkable-sync.example.json" "$config"
  echo "Wrote $config"
fi

echo
echo "Enable it with:  omarchy plugin enable $id"
echo "Pair the tablet: $dest/bin/rmos-pair --check"
