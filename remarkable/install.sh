#!/bin/sh
set -eu

PREFIX="${RMOS_PREFIX:-/usr/local/bin}"
STATE_DIR="${RMOS_STATE_DIR:-/home/root/.local/share/rmos}"
HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

mkdir -p "$STATE_DIR"
[ -f "$STATE_DIR/selected.txt" ] || {
  printf '%s\n' '# UUIDs selected for Obsidian export' > "$STATE_DIR/selected.txt"
}
printf '%s\n' '0.1.0' > "$STATE_DIR/VERSION"

install -m 0755 "$HERE/rmos-select" "$PREFIX/rmos-select"
install -m 0755 "$HERE/rmos-unselect" "$PREFIX/rmos-unselect"

echo "Installed RMOS helper scripts."
echo "Persistent state: $STATE_DIR"
echo "No xochitl files were modified."
