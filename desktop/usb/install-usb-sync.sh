#!/bin/sh
# Install attach-triggered sync: a udev rule that starts a systemd *user*
# service when the reMarkable's USB network gadget appears.
#
# Run as your normal user. The udev rule needs root, so sudo is used for that
# one step only - running the whole script as root would install the user unit
# into root's home, where your service manager will never see it.
set -eu

HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH='' cd -- "$HERE/../.." && pwd)

RULES_PATH=${RMOS_RULES_PATH:-/etc/udev/rules.d/99-rmos.rules}
UNIT_DIR=${RMOS_UNIT_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}
UNIT_PATH="$UNIT_DIR/rmos-sync.service"

HOST=10.11.99.1
WAIT=45
TIMEOUT=600
NOTIFY=0
DRY_RUN=0
VENDOR=
PRODUCT=
IFACE=
RMOS_BIN=

usage() {
  cat >&2 <<EOF
usage: install-usb-sync.sh [options]

  --vendor ID       USB idVendor (default: detected from the connected tablet)
  --product ID      USB idProduct (default: detected)
  --interface NAME  network interface to detect from (default: auto)
  --rmos PATH       path to the rmos executable (default: from PATH)
  --host ADDR       tablet address, used for detection (default: $HOST)
  --wait SECONDS    how long to wait for the tablet after attach (default: $WAIT)
  --timeout SECONDS systemd start timeout (default: $TIMEOUT)
  --notify          send a desktop notification when a sync finishes
  --dry-run         print what would be installed, write nothing
  -h, --help        this message
EOF
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --vendor) VENDOR=${2:?}; shift 2 ;;
    --product) PRODUCT=${2:?}; shift 2 ;;
    --interface) IFACE=${2:?}; shift 2 ;;
    --rmos) RMOS_BIN=${2:?}; shift 2 ;;
    --host) HOST=${2:?}; shift 2 ;;
    --wait) WAIT=${2:?}; shift 2 ;;
    --timeout) TIMEOUT=${2:?}; shift 2 ;;
    --notify) NOTIFY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done

if [ "$(id -u)" = 0 ] && [ "$DRY_RUN" = 0 ]; then
  echo "Run this as your normal user, not as root." >&2
  echo "The udev step will ask for sudo on its own." >&2
  exit 1
fi

# --- locate the rmos executable ------------------------------------------

if [ -z "$RMOS_BIN" ]; then
  RMOS_BIN=$(command -v rmos 2>/dev/null || true)
fi
# Fall back to this checkout's virtualenv: an editable install puts rmos there
# and not on PATH, which is the normal state while developing.
if [ -z "$RMOS_BIN" ] && [ -x "$REPO/.venv/bin/rmos" ]; then
  RMOS_BIN="$REPO/.venv/bin/rmos"
fi
if [ -z "$RMOS_BIN" ]; then
  echo "Could not find 'rmos' on PATH, and no virtualenv at $REPO/.venv." >&2
  echo "Pass --rmos /path/to/rmos." >&2
  exit 1
fi
case "$RMOS_BIN" in
  /*) ;;
  *) RMOS_BIN=$(CDPATH='' cd -- "$(dirname -- "$RMOS_BIN")" && pwd)/$(basename -- "$RMOS_BIN") ;;
esac
if [ ! -x "$RMOS_BIN" ]; then
  echo "Not executable: $RMOS_BIN" >&2
  exit 1
fi

# --- identify the tablet's USB gadget ------------------------------------

# udev matches on the USB device's attributes, which live on an ancestor of
# the network interface. This walk mirrors what ATTRS{} does in a rule.
usb_ids_for() {
  _dev=$(readlink -f "/sys/class/net/$1/device" 2>/dev/null) || return 1
  while [ -n "$_dev" ] && [ "$_dev" != "/" ]; do
    if [ -r "$_dev/idVendor" ] && [ -r "$_dev/idProduct" ]; then
      printf '%s %s\n' "$(cat "$_dev/idVendor")" "$(cat "$_dev/idProduct")"
      return 0
    fi
    _dev=$(dirname "$_dev")
  done
  return 1
}

detect_interface() {
  _prefix=$(printf '%s' "$HOST" | sed 's/\.[0-9]*$//')
  for _path in /sys/class/net/*; do
    _iface=${_path##*/}
    [ "$_iface" = lo ] && continue
    [ -e "$_path/device" ] || continue
    if ip -4 -o addr show dev "$_iface" 2>/dev/null | grep -q " ${_prefix}\."; then
      printf '%s\n' "$_iface"
      return 0
    fi
  done
  return 1
}

if [ -z "$VENDOR" ] || [ -z "$PRODUCT" ]; then
  if [ -z "$IFACE" ]; then
    IFACE=$(detect_interface || true)
  fi
  if [ -z "$IFACE" ]; then
    echo "Could not find the tablet's USB network interface." >&2
    echo "Connect the reMarkable by USB and try again, or pass --vendor/--product." >&2
    exit 1
  fi
  ids=$(usb_ids_for "$IFACE") || {
    echo "$IFACE is not a USB device; pass --vendor/--product explicitly." >&2
    exit 1
  }
  VENDOR=${VENDOR:-$(printf '%s' "$ids" | cut -d' ' -f1)}
  PRODUCT=${PRODUCT:-$(printf '%s' "$ids" | cut -d' ' -f2)}
  echo "Detected tablet on $IFACE: USB $VENDOR:$PRODUCT"
fi

# --- render the templates ------------------------------------------------

if [ "$NOTIFY" = 1 ]; then
  # Leading '-' so a missing or failing notifier can never fail the sync.
  # $SERVICE_RESULT is systemd's to expand, not ours.
  # shellcheck disable=SC2016
  NOTIFY_LINE='ExecStopPost=-/bin/sh -c '"'"'if [ "$SERVICE_RESULT" = success ]; then notify-send "reMarkable" "Obsidian sync complete"; else notify-send -u critical "reMarkable" "Obsidian sync failed"; fi'"'"''
else
  NOTIFY_LINE=''
fi

unit_text() {
  sed -e "s|@RMOS@|$RMOS_BIN|g" \
      -e "s|@REPO@|$REPO|g" \
      -e "s|@WAIT@|$WAIT|g" \
      -e "s|@TIMEOUT@|$TIMEOUT|g" \
      "$HERE/rmos-sync.service.in" \
    | sed -e "s|@NOTIFY@|$NOTIFY_LINE|"
}

rules_text() {
  sed -e "s|@VENDOR@|$VENDOR|g" -e "s|@PRODUCT@|$PRODUCT|g" "$HERE/99-rmos.rules.in"
}

if [ "$DRY_RUN" = 1 ]; then
  echo "--- $UNIT_PATH ---"
  unit_text
  echo "--- $RULES_PATH ---"
  rules_text
  exit 0
fi

# --- install -------------------------------------------------------------

mkdir -p "$UNIT_DIR"
unit_text > "$UNIT_PATH"
echo "Wrote $UNIT_PATH"

SUDO=
if [ "$(id -u)" != 0 ]; then
  SUDO=$(command -v sudo || true)
  if [ -z "$SUDO" ]; then
    echo "sudo not found. Install the rule yourself:" >&2
    echo "  rules_text > $RULES_PATH" >&2
    exit 1
  fi
fi

rules_text | $SUDO tee "$RULES_PATH" >/dev/null
echo "Wrote $RULES_PATH"

$SUDO udevadm control --reload-rules
systemctl --user daemon-reload
echo
echo "Installed. Unplug and replug the tablet to test, then:"
echo "  systemctl --user status rmos-sync.service"
echo "  journalctl --user -u rmos-sync.service -n 50"
