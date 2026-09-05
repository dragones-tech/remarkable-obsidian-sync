# Product specification: reMarkable -> Obsidian USB Sync

## Product idea

Turn a reMarkable 1 into a focused handwritten capture device for Obsidian without putting Obsidian, a browser stack, or a continuous sync daemon on the tablet.

A user explicitly marks a notebook for the `Obsidian` source. On the next USB connection, a desktop utility synchronizes only marked notebooks into an Obsidian vault.

## Core requirements

### On reMarkable 1

- Provide an action conceptually named **Copy to Obsidian**.
- The action records the selected document UUID in our own state directory.
- Do not duplicate the notebook if a stable UUID reference is sufficient.
- Do not edit xochitl metadata for the MVP.
- Keep device CPU/RAM usage effectively zero when idle.
- Survive normal OS updates as much as possible by keeping persistent state under `/home/root/.local/share/rmos/` and making the UI integration reinstallable.

### Desktop

- Work without reMarkable cloud.
- Primary transport: SSH over USB at `root@10.11.99.1`.
- Read only the selected xochitl document bundles.
- Incremental sync: do not copy unchanged notebooks.
- Store a local state database mapping reMarkable UUID -> last imported fingerprint.
- Export into one configurable Obsidian folder, e.g. `Sources/reMarkable/`.
- Generate deterministic Markdown so repeated syncs update rather than duplicate notes.
- Preserve the original UUID in frontmatter.

## Proposed output

For notebook `Project Alpha`:

```text
<Vault>/Sources/reMarkable/Project Alpha/
  Project Alpha.md
  raw/
    <uuid>.metadata
    <uuid>.content
    <uuid>.pagedata       (when present)
    <uuid>/...            (stroke/page data)
  attachments/
    Project Alpha.pdf     (phase 2)
    page-001.png          (optional, phase 2)
```

Markdown:

```markdown
---
source: remarkable
remarkable_id: <uuid>
remarkable_modified: <timestamp>
rmos_fingerprint: <sha256>
---

# Project Alpha

> Synced from reMarkable.

![[attachments/Project Alpha.pdf]]
```

Until rendering exists, the Markdown should clearly say that raw notebook data was synchronized but a visual export has not yet been generated.

## Sync semantics

- One-way only in MVP.
- A notebook is selected by presence of its UUID in `/home/root/.local/share/rmos/selected.txt`.
- Each non-empty, non-comment line is one UUID.
- Desktop reads metadata to resolve visible name and modified time.
- Fingerprint should be based on the selected document bundle contents, not only timestamps.
- If unchanged: skip.
- If changed: pull to a temporary directory, validate, atomically replace local raw copy, regenerate Markdown, update desktop state.
- If the notebook was renamed on reMarkable: preserve identity by UUID and rename/move the destination rather than create a duplicate.
- Deleting/unselecting a notebook on reMarkable must NOT delete anything from Obsidian automatically.

## Non-goals for MVP

- Bidirectional Obsidian -> reMarkable sync.
- Continuous Wi-Fi service.
- Cloud dependency.
- OCR on the tablet.
- Editing `.rm` strokes from Obsidian.
- Replacing xochitl.
- Modifying reMarkable document database structure.

## Phase 2

- Render notebook to PDF/PNG on desktop using a maintained parser/renderer compatible with the installed reMarkable file format.
- Optional OCR/transcription on desktop.
- Obsidian plugin or post-processing hook.
- Automatic sync when USB interface appears.

## Phase 3

- Native/patch-based UI action inside xochitl if firmware compatibility is understood.
- Alternative lightweight launcher/action menu if xochitl patching proves too fragile.
- Optional LAN/local-transfer mode.

## Safety / recovery

Before UI patching:

1. Record firmware version.
2. Verify SSH access.
3. Back up `/home/root/.local/share/remarkable/xochitl/`.
4. Back up any file that will be changed.
5. Ensure the integration has an uninstall script.
6. Never test destructive writes without a restorable backup.

## Acceptance criteria for MVP

- `rmos doctor` verifies SSH connectivity and required paths.
- `rmos list` shows selected UUIDs and visible notebook names.
- `rmos sync --dry-run` reports what would change without writing the vault.
- `rmos sync` imports selected raw bundles and creates deterministic Markdown.
- A second sync with no changes performs no document update.
- A notebook rename does not create a second identity.
- No command writes to xochitl's notebook storage on the device.
