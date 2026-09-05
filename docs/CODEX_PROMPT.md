# Next implementation tasks

The desktop MVP described in `SPEC.md` is implemented and covered by tests:
UUID validation, selection parsing, metadata parsing, deterministic destination
naming, fingerprinting, rename identity, collision safety, state updates, and
the device-side shell scripts. `make check` runs lint and the suite.

What remains is staged deliberately, because each item needs evidence from a
real device before it can be written safely.

## Phase 2a - rendering

> Add a pluggable renderer behind a `Renderer` protocol, defaulting to the
> current no-op. Do not select a `.rm` parser until it has been validated
> against sample data from the actual firmware in use: the stroke format is
> version-sensitive and a parser for the wrong version fails silently or
> produces wrong output. `render_markdown` already accepts `pdf_name`, so the
> Markdown side of the seam exists.

Before starting, capture from the device: firmware version, `uname -a`,
`/etc/os-release`, and one real notebook bundle to test against.

## Phase 2b - sync on USB attach

> Add a udev rule plus a systemd `--user` unit that runs `rmos sync` when the
> tablet's USB network interface appears. Must be safe to trigger repeatedly
> and must not block if the tablet is unreachable.

## Phase 3 - on-device UI action

Try the approaches in increasing order of risk, and stop at the first that
works:

1. **A dedicated `Obsidian` collection in xochitl**, read by the desktop client
   as the selection mechanism. No patching, nothing to uninstall, survives
   firmware updates. Try this first.
2. A lightweight companion menu reachable through an existing gesture.
3. A firmware-compatible xochitl hook that invokes `rmos-select <uuid>`.

Whatever is chosen must stay a thin adapter over the `selected.txt` contract,
so the desktop client never needs to know which mechanism produced it.

Before any device experiment: record the firmware version, verify SSH access,
back up `/home/root/.local/share/remarkable/xochitl/`, and confirm the
integration has a working uninstall path.
