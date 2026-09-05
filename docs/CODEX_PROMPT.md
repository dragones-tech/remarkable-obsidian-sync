# Next implementation tasks

The desktop MVP described in `SPEC.md` is implemented and covered by tests:
UUID validation, selection parsing, metadata parsing, deterministic destination
naming, fingerprinting, rename identity, collision safety, state updates, and
the device-side shell scripts. `make check` runs lint and the suite.

What remains is staged deliberately, because each item needs evidence from a
real device before it can be written safely.

## Phase 2a - rendering (seam done, parser outstanding)

The `Renderer` protocol, the `none` and `command` backends, attachment
handling and `rmos inspect` are implemented and tested. What remains needs a
real device:

1. Sync one real notebook, then run `rmos inspect` to learn which stroke
   format the firmware writes.
2. Pick a renderer that supports exactly that version and wire it up through
   `[render] backend = "command"`.
3. Only if the command backend proves too limiting, add a native backend - and
   even then, validate it against sample data from that same firmware first. A
   parser built for the wrong version fails quietly.

Also capture, for the record: firmware version, `uname -a`, `/etc/os-release`.

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
