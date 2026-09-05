# remarkable-obsidian-sync

Minimal, USB-first bridge from a reMarkable 1 to an Obsidian vault.

## Goal

Keep the tablet simple. Notes remain native reMarkable documents. A small on-device action marks selected notebooks for export. When the tablet is connected by USB, the desktop client pulls only those marked notebooks into a dedicated Obsidian source folder.

## MVP flow

1. Create/write a notebook normally on the reMarkable.
2. Mark that notebook with the on-device action **Copy to Obsidian**.
3. Connect the reMarkable 1 over USB.
4. Run `rmos sync` on the computer (later: auto-run on USB attach).
5. The client pulls the selected notebook data and creates/updates a Markdown note in the configured Obsidian vault.

The MVP is intentionally one-way: reMarkable -> Obsidian.

## Safety principles

- Do not modify notebook data in place.
- Do not write into reMarkable's xochitl data directory from the desktop client.
- Store our device-side state under `/home/root/.local/share/rmos/`.
- Make all device-side integration reinstallable.
- Back up before experimenting with UI hooks.
- Prefer SSH-over-USB transport (`10.11.99.1`) rather than cloud sync.

## Repository layout

- `remarkable/` - tiny device-side scripts/state.
- `desktop/` - desktop sync client.
- `docs/` - architecture and implementation notes.
- `tests/` - desktop-side and device-script tests.
- `SPEC.md` - product/technical specification.

## Quick start

Requires Python 3.11+, OpenSSH and `tar` on the desktop.

```bash
python -m venv .venv
source .venv/bin/activate
make install
```

Create a config file and point it at your vault:

```bash
rmos init-config --vault ~/Documents/Obsidian/MyVault
```

Connect the tablet by USB, then:

```bash
rmos doctor          # check local tools, the tablet and the vault
rmos list            # selected notebooks and their names
rmos status          # which of them are new / changed / unchanged
rmos sync --dry-run  # report what would change, transferring nothing
rmos sync            # import into the vault
```

## Commands

| Command | What it does |
| --- | --- |
| `rmos doctor` | Verifies local `ssh`/`tar`, tablet reachability, the xochitl directory, a remote checksum tool, the selection file, and that the vault is writable. Exits non-zero if a required check fails. |
| `rmos list` | Prints the UUID and visible name of each selected notebook. |
| `rmos status` | Shows per-notebook `new` / `changed` / `unchanged`, plus notebooks previously synced but no longer selected. |
| `rmos sync` | Imports changed notebooks. `--dry-run` reports without transferring or writing. |
| `rmos select <uuid>` | Marks a notebook from the desktop, without opening a shell on the tablet. |
| `rmos unselect <uuid>` | Unmarks it. Existing vault notes are always kept. |
| `rmos init-config` | Writes a starter `~/.config/rmos/config.toml`. |

## How sync behaves

- **Identity is the UUID, never the name.** Renaming a notebook on the tablet moves the existing vault folder and rewrites the note; it never creates a second copy.
- **Two notebooks sharing a name get separate folders** — the second is suffixed with the first 8 characters of its UUID. Neither can overwrite the other, or a note you wrote by hand.
- **Unchanged notebooks are never transferred.** Fingerprints are computed *on the tablet* (`sha256sum`, falling back to `md5sum`), so an unchanged notebook costs one checksum pass and no data transfer — `--dry-run` in particular downloads nothing.
- **Transfers are verified.** The bundle is re-fingerprinted after arrival; if it changed mid-transfer because you were writing on the tablet, the sync fails for that notebook and the vault is left untouched.
- **Nothing is ever deleted from the vault.** Unselecting or deleting a notebook on the tablet leaves the exported note in place. The only file rmos removes is a note it generated itself under a previous name, after a rename.
- **One SSH connection per run.** Connections are multiplexed, so password authentication prompts once rather than once per file. Set `multiplex = false` in the config to disable.
- **One failed notebook does not stop the rest.** Failures are reported per notebook and the command exits non-zero at the end.

## Device-side install

From a shell on the tablet:

```sh
./install.sh          # installs rmos-select / rmos-unselect, creates state dir
./uninstall.sh        # removes the scripts, keeps the selection state
```

Neither script reads or writes anything under xochitl.

## Development

```bash
make check   # ruff + pytest
make test
make lint
```

## Current status

The desktop MVP is implemented and tested against the acceptance criteria in `SPEC.md`: incremental sync, rename identity, collision safety, non-destructive semantics, and a device that is read-only apart from our own state directory.

Still staged as follow-up work:

- Rendering handwriting to PDF/PNG (`SPEC.md` phase 2) — deliberately not implemented until a parser has been validated against the actual firmware's file format.
- Automatic sync on USB attach (phase 2).
- The on-device **Copy to Obsidian** UI action (phase 3). Until then, mark notebooks with `rmos select <uuid>` from the desktop or `rmos-select` on the tablet.

See `SPEC.md` and `docs/ARCHITECTURE.md`.
