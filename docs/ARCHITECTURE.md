# Architecture

## Why this split

The reMarkable 1 has limited CPU/RAM and its primary value is low-latency handwriting. Therefore the device component only stores selection state. Parsing, hashing, rendering, OCR, naming, conflict handling, and Obsidian integration live on the desktop.

## Device state

```text
/home/root/.local/share/rmos/
  selected.txt
  VERSION
```

`selected.txt` is one of two ways a notebook gets marked; see **Selection sources** below.

Example:

```text
# UUIDs selected for Obsidian export
550e8400-e29b-41d4-a716-446655440000
```

This deliberately avoids changing files under the notebook store.

## Selection sources

A notebook is marked for export by either mechanism, and the desktop unions
them:

1. **A tag applied on the tablet** - the primary action. The reMarkable UI can
   tag a document (`.content` -> `tags`) and tag an individual page
   (`.content` -> `pageTags`); both count, since either is a reasonable way for
   someone to say "sync this". Firmware `20260612085811` encodes a tag as
   `{"name": "sync", "pageId": ..., "timestamp": ...}`, and the plain-string
   form is accepted too.
2. **`selected.txt`** under our own state directory, written by `rmos-select`
   on the tablet or `rmos select` from the desktop.

Tagging is what phase 3 of `SPEC.md` was reaching for, reached without any of
its risk: no xochitl patch, no binary replaced, nothing to uninstall, and no
firmware update to survive - the mechanism *is* the stock UI. It also does not
disturb the user's filing, which a dedicated `Obsidian` folder would have.

Reading tags means reading the whole document index (`*.metadata` and
`*.content`, a few MB) and parsing it as JSON. Filtering on the device with
grep or awk would be far cheaper, but it would depend on how the firmware
happens to pretty-print `.content`; if that ever changed, tagged notebooks
would silently stop syncing. For a selection mechanism, silence is the worst
possible failure, so the cost is paid. A tag list we cannot decode is reported
rather than swallowed.

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
render (pluggable; no-op by default)
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

## Rendering

rmos ships no `.rm` parser. The stroke format is version-sensitive, and a parser
built for the wrong version fails quietly rather than loudly, so committing to
one on the user's behalf would be guessing with their notes.

Instead the seam is explicit:

- `Renderer` is a protocol with two members: `render(...)`, which writes one
  attachment and returns it, and `signature`, which changes whenever
  configuration affecting the output changes.
- `NullRenderer` is the default and produces nothing.
- `CommandRenderer` delegates to an external tool the user configures. It is
  invoked without a shell, with only four documented placeholders substituted,
  and must write the exact path it is given.

`signature` is recorded in state alongside the content fingerprint. That
separates two reasons to redo work: stale content needs a transfer, stale
rendering does not. Enabling or changing a renderer therefore re-renders from
the bundle already in the vault rather than pulling an identical copy off the
tablet.

A render failure is recorded as `failed:<signature>` so a broken command is not
retried on every sync. Changing the configuration, or `--re-render`, retries.

`rmos inspect` reads the `.rm` header of each synced page and reports the format
version, which is the evidence needed to pick a parser. It reports an
unrecognised header as unknown rather than guessing.

## Desktop state

Default:

```text
~/.local/state/rmos/state.json
```

Each UUID records the content fingerprint, visible name, destination folder, last successful sync time, the renderer signature, and the attachment filename (if any). It is written atomically after each notebook, so an interrupted run never loses track of work already committed to the vault.

## UI integration strategy

Settled: **the stock tag UI is the integration.** No xochitl patch was needed,
so the risk this section was written to manage does not arise.

The options originally listed, and why they were not taken:

- *Patching xochitl* - highest risk, needs re-doing after firmware updates.
- *A companion menu on a gesture* - still a modification to maintain.
- *A dedicated `Obsidian` folder* - no modification, but it forces the user to
  move notebooks out of their own filing to sync them. Rejected for that
  reason; tagging marks a notebook in place.

The desktop still treats selection as a set of UUIDs from interchangeable
sources, so another mechanism can be added later without touching sync.

## Update resilience

We cannot guarantee any third-party modification survives every official update. We can make recovery cheap:

- persistent selection state under `/home/root/.local/share/rmos/`;
- no replacement of core binaries for MVP;
- install/uninstall scripts are idempotent;
- UI patch is separated from data/state;
- desktop client has no dependency on a permanent background service on the tablet.
