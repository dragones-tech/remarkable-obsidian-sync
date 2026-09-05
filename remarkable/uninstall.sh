#!/bin/sh
set -eu
PREFIX="${RMOS_PREFIX:-/usr/local/bin}"
rm -f "$PREFIX/rmos-select" "$PREFIX/rmos-unselect"
echo "Removed RMOS helper scripts."
echo "Selection state was intentionally preserved under /home/root/.local/share/rmos/."
