# Next implementation tasks

The desktop MVP described in `SPEC.md` is implemented and covered by tests:
UUID validation, selection parsing, metadata parsing, deterministic destination
naming, fingerprinting, rename identity, collision safety, state updates, and
the device-side shell scripts. `make check` runs lint and the suite.

What remains is staged deliberately, because each item needs evidence from a
real device before it can be written safely.

## Phase 2a - rendering (thumbnails shipped, full-resolution outstanding)

The `Renderer` protocol, the `none` and `command` backends, attachment
handling and `rmos inspect` are implemented and tested. What remains needs a
real device:

1. Sync one real notebook, then run `rmos inspect` to learn which stroke
   format the firmware writes. On firmware `20260612085811` this reports
   **v6** for every page, so a v5-only parser will not do.
2. Pick a renderer that supports exactly that version and wire it up through
   `[render] backend = "command"`. Note that each page also has a
   device-rendered PNG under `<uuid>.thumbnails/`, which is a zero-parser
   fallback if thumbnail resolution is acceptable.
3. Only if the command backend proves too limiting, add a native backend - and
   even then, validate it against sample data from that same firmware first. A
   parser built for the wrong version fails quietly.

Recorded so far: firmware `20260612085811`, `Codex Linux 5.7.126 (scarthgap)`,
kernel `5.4.70-v1.6.3-rm10x armv7l`, USB gadget `04b3:4010`. `/bin/sh` is bash,
and `tar`, `sha256sum`, `find` and `grep` are full coreutils, not busybox
applets - so the remote shell snippets have more room than assumed.

## Phase 2b - sync on USB attach (done)

Implemented in `desktop/usb/`. The rendered rule and unit are checked by
`udevadm verify` and `systemd-analyze verify` in the test suite.

## Phase 3 - on-device UI action (done: the stock tag UI)

Selection is a tag applied in the reMarkable's own UI - on the document or on
any page. No patch, nothing to uninstall, nothing for a firmware update to
break. `rmos tags` lists what is tagged.

Nothing further is required here. If another mechanism is ever wanted, add it
as a `[selection] sources` entry; sync consumes a set of UUIDs and does not
care where they came from.

Before any device experiment: record the firmware version, verify SSH access,
back up `/home/root/.local/share/remarkable/xochitl/`, and confirm the
integration has a working uninstall path.
