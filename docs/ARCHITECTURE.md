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

The client shells out to the user's `ssh` and `scp` executables instead of adding a large dependency or daemon to the tablet.

Later we can add rsync where available, but the MVP should not assume rsync exists on every host/device combination.

## Desktop pipeline

```text
USB connection
   |
   v
SSH health check
   |
   v
read selected.txt
   |
   v
resolve each UUID metadata
   |
   v
pull bundle -> temp dir
   |
   v
fingerprint + validate
   |
   +---- unchanged ---> skip
   |
   v
atomic local raw update
   |
   v
render (phase 2)
   |
   v
write deterministic Markdown
   |
   v
update state.json
```

## Desktop state

Default:

```text
~/.local/state/rmos/state.json
```

Each UUID records fingerprint, visible name, destination folder, and last successful sync time.

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
