# remarkable-obsidian-sync

Minimal, USB-first bridge from a reMarkable 1 to an Obsidian vault.

## Goal

Keep the tablet simple. Notes remain native reMarkable documents. A small on-device action marks selected notebooks for export. When the tablet is connected by USB, the desktop client pulls only those marked notebooks into a dedicated Obsidian source folder.

## MVP flow

1. Create/write a notebook normally on the reMarkable.
2. Tag it (or any of its pages) `obsidian` on the tablet.
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
- `omarchy/` - Omarchy desktop plugin (bar widget and picker).
- `tests/` - desktop-side, device-script and plugin tests.
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
| `rmos status` | Shows per-notebook `new` / `changed` / `unchanged`, plus notebooks previously synced but no longer selected. With `--json`, also where each note lives in the vault. |
| `rmos sync` | Imports changed notebooks. `--dry-run` reports without transferring or writing; `--re-render` re-runs the renderer without transferring. |
| `rmos tags` | Lists the tags in use on the tablet, with document counts. `--all` also shows which notebook carries which. |
| `rmos inspect` | Reports the `.rm` stroke format of synced notebooks. Reads the local vault only — no tablet needed. |
| `rmos select <uuid>` | Marks a notebook from the desktop, without opening a shell on the tablet. |
| `rmos unselect <uuid>` | Unmarks it. Existing vault notes are always kept. |
| `rmos index` | Lists every notebook with its folder, tags and selection state. |
| `rmos config get/set/unset` | Reads and writes one setting. |
| `rmos init-config` | Writes a starter `~/.config/rmos/config.toml`. |

Global flags: `--config`, `-v/--verbose`, `--json`, `--batch` (never prompt — required for unattended runs) and `--wait SECONDS` (wait for the tablet to answer before giving up). They are accepted before or after the subcommand, so both `rmos --json sync` and `rmos sync --json` work.

`--json` makes a command emit exactly one JSON object and nothing else, which is what the Omarchy plugin parses.

## Marking a notebook for export

Tag it on the tablet. A tag on the **document** or on **any one of its pages** counts, so whichever way the reMarkable UI lets you tag, it works. Nothing is moved: the notebook stays in whatever folder you filed it in.

```bash
rmos tags          # which tags exist on the tablet, and how many notebooks carry each
rmos tags --all    # ...and which notebook carries which
```

The tag rmos looks for is configurable, and `selected.txt` still works as a second source:

```toml
[selection]
sources = ["file", "tag"]   # both are read; the result is the union
tags = ["obsidian", "sync"] # any of these selects a notebook
```

The older singular `tag = "obsidian"` is still accepted.

Matching ignores case and surrounding whitespace. Deleted and trashed notebooks are skipped. Set `sources = ["tag"]` to use tagging alone — the `file` source is the only one that costs a round trip when unused, and the `tag` source reads the tablet's document index (a few MB), so dropping either saves a little work.

If a notebook carries tags in an encoding rmos cannot decode, it says so loudly rather than quietly selecting nothing.

## Configuration

Two files:

- **`~/.config/rmos/config.toml`** is yours. It carries the comments explaining every option, and rmos never rewrites it.
- **`~/.config/rmos/config.local.toml`** is written by `rmos config set` — that is how the Omarchy plugin persists what you pick in its UI. It is machine-owned and overrides the file above.

```bash
rmos config get selection.tags
rmos config set selection.tags '["obsidian","sync"]'
rmos config unset selection.tags     # fall back to config.toml
```

Only settings that cannot launch a process are writable this way. The render command and `ssh_options` stay hand-edited, so a bug in a UI can never turn into command execution.

## How sync behaves

- **Identity is the UUID, never the name.** Renaming a notebook on the tablet moves the existing vault folder and rewrites the note; it never creates a second copy.
- **Two notebooks sharing a name get separate folders** — the second is suffixed with the first 8 characters of its UUID. Neither can overwrite the other, or a note you wrote by hand.
- **Unchanged notebooks are never transferred.** Fingerprints are computed *on the tablet* (`sha256sum`, falling back to `md5sum`), so an unchanged notebook costs one checksum pass and no data transfer — `--dry-run` in particular downloads nothing.
- **Transfers are verified.** The bundle is re-fingerprinted after arrival; if it changed mid-transfer because you were writing on the tablet, the sync fails for that notebook and the vault is left untouched.
- **Nothing is ever deleted from the vault.** Unselecting or deleting a notebook on the tablet leaves the exported note in place. The only file rmos removes is a note it generated itself under a previous name, after a rename.
- **One SSH connection per run.** Connections are multiplexed, so password authentication prompts once rather than once per file. Set `multiplex = false` in the config to disable.
- **One failed notebook does not stop the rest.** Failures are reported per notebook and the command exits non-zero at the end.

## Rendering

Three backends, set with `[render] backend`:

| | What it produces | Cost |
| --- | --- | --- |
| `none` | Raw data only (default) | — |
| `thumbnails` | One PNG per page, from the previews the tablet already drew | No parser, nothing to install |
| `command` | Whatever an external tool produces | You choose and validate the tool |

```bash
rmos config set render.backend thumbnails
rmos sync --re-render     # re-renders from the bundle already in the vault
```

### thumbnails

The tablet draws a preview of every page and keeps it in the bundle, so this
needs no `.rm` parser at all — nothing here can misread a stroke format. Pages
are copied out in reading order and embedded in the note, so you scroll the
note and read the notebook in flow.

They are **384×512** against the tablet's 1404×1872 screen. That is fine for
drawings and large writing; for dense handwriting it may not be. Trying it
costs one `--re-render`, which transfers nothing.

Only notebooks are rendered. A book or PDF has page previews too, but a page
of someone else's book is not a note and there would be hundreds of them.

Pages are named after the notebook (`Quick sheets p01.png`) because Obsidian
resolves attachments by filename, and `page-01.png` in every folder would be
ambiguous.

### command

For a real renderer at full resolution. Point it at a tool that supports your
firmware's stroke format — `rmos inspect` reports which:

```toml
[render]
backend = "command"
command = ["my-renderer", "--input", "{raw}", "--output", "{out}"]
extension = "pdf"
timeout = 300
```

Placeholders: `{raw}` (the synced bundle directory), `{uuid}`, `{name}` (the
vault folder name), `{out}` (the exact path the command must write). The
command runs **without a shell** — arguments are passed as a list, so a
notebook name containing shell metacharacters is inert. `render.command` is
deliberately not writable by `rmos config set`, so no UI can introduce one.

### Behaviour worth knowing

- Enabling, disabling or changing the renderer re-renders **without
  re-downloading** anything. Only the rendering was stale, and the bundle is
  already local.
- A renderer that fails is not fatal: the raw bundle is still imported, you
  get a warning, and the failure is recorded so the next sync does not
  silently retry a broken command. Change the config, or pass `--re-render`,
  to try again.
- Pages rmos produced follow a rename, and ones it no longer produces are
  removed. Nothing else in `attachments/` is ever touched.

## Sync automatically on USB attach

A udev rule starts a systemd **user** service when the tablet's USB network gadget appears. It runs as you, with your config, vault and SSH keys — not as root.

**Prerequisite: key authentication.** An attach-triggered run has no terminal, so it cannot answer a password prompt. Set it up once:

```bash
ssh-copy-id root@10.11.99.1     # password is in Settings -> Help -> About
rmos doctor                     # the "unattended-ready" check must pass
```

Then, with the tablet connected so its USB IDs can be detected:

```bash
./desktop/usb/install-usb-sync.sh --dry-run   # see exactly what would be installed
./desktop/usb/install-usb-sync.sh --notify    # install (asks for sudo for the udev rule)
```

Run it as your normal user, not with sudo — the script escalates only for the udev rule. Running the whole thing as root would put the user unit in root's home, where your service manager never looks.

Options: `--wait` (how long to wait for the tablet after attach, default 45s), `--timeout`, `--notify` (desktop notification when a sync finishes), `--vendor`/`--product` (skip detection), `--rmos` (path to the executable).

Check on it:

```bash
systemctl --user status rmos-sync.service
journalctl --user -u rmos-sync.service -n 50
./desktop/usb/uninstall-usb-sync.sh
```

Two details worth knowing:

- The USB interface appears **before** the tablet's sshd accepts connections, so the service retries with backoff for `--wait` seconds rather than connecting once and missing it.
- `SYSTEMD_USER_WANTS` activates the unit in every running user manager instance. On a single-user desktop that is what you want; on a shared machine, every logged-in user with the unit installed would sync.

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

Rendering (`SPEC.md` phase 2) is wired end to end behind a pluggable backend, with `rmos inspect` to identify which stroke format your firmware writes. No parser ships with rmos — see [Rendering](#rendering).

Verified end to end against a reMarkable running firmware `20260612085811` (Codex Linux, kernel 5.4.70 armv7l): `doctor`, `select`, `list`, `status`, `sync --dry-run`, `sync`, incremental re-sync, non-destructive `unselect`, and an unattended run driven by systemd. That device writes **v6** stroke data.

Still staged as follow-up work:

Marking a notebook is done with the tablet's own tag UI — no xochitl patch, nothing to uninstall, and nothing that a firmware update can break. Verified against a real device: a page tagged `sync` selected its notebook, synced, and a second sync reported it unchanged.

See `SPEC.md` and `docs/ARCHITECTURE.md`.
