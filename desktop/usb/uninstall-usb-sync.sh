#!/bin/sh
# Remove attach-triggered sync. Leaves rmos, your config, state and vault alone.
set -eu

RULES_PATH=${RMOS_RULES_PATH:-/etc/udev/rules.d/99-rmos.rules}
UNIT_DIR=${RMOS_UNIT_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}
UNIT_PATH="$UNIT_DIR/rmos-sync.service"

if [ -f "$UNIT_PATH" ]; then
  rm -f "$UNIT_PATH"
  echo "Removed $UNIT_PATH"
  systemctl --user daemon-reload 2>/dev/null || true
fi

if [ -e "$RULES_PATH" ]; then
  SUDO=
  if [ "$(id -u)" != 0 ]; then
    SUDO=$(command -v sudo || true)
  fi
  $SUDO rm -f "$RULES_PATH"
  echo "Removed $RULES_PATH"
  $SUDO udevadm control --reload-rules 2>/dev/null || true
fi

echo "Attach-triggered sync removed. Nothing else was touched."
