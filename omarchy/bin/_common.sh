# Shared helpers for the plugin's bin scripts. Sourced, never executed.
#
# Every script here prints exactly one line of JSON on stdout, because the QML
# side parses it with JSON.parse. Expected failures - no tablet, no config -
# are reported as an `error` field with exit 0, so the widget can say what is
# wrong instead of showing nothing.

CONFIG="${RMOS_PLUGIN_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/remarkable-sync.json}"

DEFAULT_VENDOR=04b3
DEFAULT_PRODUCT=4010

setting() {
  # setting <jq-path> <fallback>
  local value
  value=$(jq -r "$1 // empty" "$CONFIG" 2>/dev/null) || value=""
  printf '%s' "${value:-$2}"
}

emit() { jq -cn "$@"; }

die_json() {
  jq -cn --arg error "$1" '{error: $error}'
  exit 0
}

# The rmos executable: an explicit setting, then PATH, then a checkout's venv
# next to this plugin. Resolved once so every script agrees.
resolve_rmos() {
  local configured
  configured=$(setting '.rmosPath' "")
  if [[ -n $configured ]]; then
    [[ -x $configured ]] || die_json "rmosPath in $CONFIG is not executable: $configured"
    printf '%s' "$configured"
    return 0
  fi
  local found
  if found=$(command -v rmos 2>/dev/null); then
    printf '%s' "$found"
    return 0
  fi
  die_json "rmos is not on PATH. Set \"rmosPath\" in $CONFIG."
}

# Run rmos and pass its JSON straight through, turning a failure into an
# `error` field rather than an empty stdout the widget cannot explain.
run_rmos() {
  local out status
  out=$("$(resolve_rmos)" --json --batch "$@" 2>/tmp/rmos-plugin-err.$$)
  status=$?
  local stderr
  stderr=$(head -c 2000 "/tmp/rmos-plugin-err.$$" 2>/dev/null)
  rm -f "/tmp/rmos-plugin-err.$$"
  if (( status != 0 )) || [[ -z $out ]]; then
    jq -cn --arg error "${stderr:-rmos exited $status}" '{error: ($error | rtrimstr("\n"))}'
    return 0
  fi
  printf '%s\n' "$out"
}
