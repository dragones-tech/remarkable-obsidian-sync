# Architecture

## Why this split

The reMarkable 1 has limited CPU/RAM and its primary value is low-latency handwriting. Therefore the device component only stores selection state. Parsing, hashing, rendering, OCR, naming, conflict handling, and Obsidian integration live on the desktop.

## Device state

```text
/home/root/.local/share/rmos/
  selected.txt
  VERSION
```

`selected.txt` is the contract between device UI integration and desktop client.

Example:

```text
# UUIDs selected for Obsidian export
550e8400-e29b-41d4-a716-446655440000
```

This deliberately avoids changing files under the notebook store.

## reMarkable notebook store

Current community tooling documents user data under:

```text
/home/root/.local/share/remarkable/xochitl/
```

Documents are UUID-based rather than human-filename based. Metadata contains the visible name/hierarchy. The desktop code must treat the exact file-format details as version-sensitive and avoid assumptions beyond what it validates.

## Transport

Primary transport is OpenSSH over USB:

```text
root@10.11.99.1
```

The client shells out to the user's `ssh` and `tar` executables instead of adding a large dependency or daemon to the tablet.

Two decisions matter here:

- **`tar` over one SSH pipe, not per-file `scp`.** A notebook bundle is several files; `scp`-ing each one opens a connection each time, which means a password prompt each time under the tablet's default password authentication. It also makes us depend on `sftp-server` being present, which modern `scp` requires but the tablet does not necessarily provide. Streaming `tar -cf -` through a single `ssh` invocation avoids both.
- **Connection multiplexing on by default.** `ControlMaster`/`ControlPersist` mean a whole sync run authenticates once, regardless of how many notebooks and how many remote commands are involved.

Later we can add rsync where available, but the MVP should not assume rsync exists on every host/device combination.

## Fingerprinting

Fingerprints are computed **on the tablet**, not by downloading and hashing locally. The device runs `sha256sum` (falling back to `md5sum`) over the bundle's files; the desktop folds the resulting `(path, digest)` pairs into one digest.

The same fold is used for a local directory, so a device-side and a desktop-side fingerprint of identical content are byte-identical strings. That gives us two things:

- **Incremental sync costs no transfer.** An unchanged notebook is detected from a checksum listing, so `sync` and `--dry-run` move no data at all for it.
- **Transfers are verified.** After extraction we re-fingerprint locally and compare. The tablet is a live device — the user may be writing while we read — so a mismatch means the bundle changed mid-transfer. We fail that notebook and leave the vault untouched rather than importing a torn copy.

The per-file algorithm name is mixed into the fold, so a fingerprint taken with `md5` can never compare equal to one taken with `sha256`.

## Desktop pipeline

```text
USB connection
   |
   v
SSH health check (doctor)
   |
   v
read selected.txt
   |
   v
resolve each UUID metadata
   |
   v
remote fingerprint (no transfer)
   |
   +---- unchanged ---> skip
   |
   v
pull bundle -> temp dir (tar over ssh)
   |
   v
re-fingerprint locally + compare
   |
   +---- mismatch ---> fail this notebook, vault untouched
   |
   v
resolve destination by UUID
   |
   +---- renamed ---> move the existing folder
   |
   v
atomic raw swap
   |
   v
render (phase 2)
   |
   v
write deterministic Markdown, prune our own stale note
   |
   v
update state.json
```

## Identity and destination naming

The vault destination is derived from the visible name, but **identity is always the UUID**. Consequences:

- A rename on the tablet moves the folder we already own; it never produces a duplicate. The stale note we generated under the old name is deleted — and only that file, identified by the `remarkable_id` in its own frontmatter, so a user's notes living beside ours are never touched.
- Two notebooks with the same visible name resolve to different folders; the loser is suffixed with the first 8 characters of its UUID.
- A folder that already belongs to another UUID, or that holds a note we did not generate, is treated as occupied. We would rather create `Notes (550e8400)` than overwrite something the user wrote.

Nothing in the client deletes user content from the vault. Unselecting a notebook, or deleting it on the tablet, leaves the exported note in place.

## Desktop state

Default:

```text
~/.local/state/rmos/state.json
```

Each UUID records fingerprint, visible name, destination folder, and last successful sync time. It is written atomically after each notebook, so an interrupted run never loses track of work already committed to the vault.

## UI integration strategy

Do not begin by patching xochitl. First prove the end-to-end sync using `remarkable/rmos-select` from SSH.

Once proven, evaluate UI approaches in this order:

1. Firmware-compatible xochitl hook/patch that invokes `rmos-select <uuid>`.
2. Lightweight companion menu/action accessible through an existing gesture/button mechanism.
3. As a fallback, a dedicated `Obsidian` collection/folder interpreted by the desktop client as the selection mechanism.

The integration must remain a thin adapter around the stable `selected.txt` contract.

## Update resilience

We cannot guarantee any third-party modification survives every official update. We can make recovery cheap:

- persistent selection state under `/home/root/.local/share/rmos/`;
- no replacement of core binaries for MVP;
- install/uninstall scripts are idempotent;
- UI patch is separated from data/state;
- desktop client has no dependency on a permanent background service on the tablet.
