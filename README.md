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
- `tests/` - desktop-side tests.
- `SPEC.md` - product/technical specification for Codex.

## Quick start for development

Requires Python 3.11+ and OpenSSH on the desktop.

```bash
cd desktop
python -m venv .venv
source .venv/bin/activate
pip install -e .
rmos doctor
```

Create a config file:

```bash
mkdir -p ~/.config/rmos
cp config.example.toml ~/.config/rmos/config.toml
```

Edit the vault path, connect the tablet by USB, then:

```bash
rmos list
rmos sync --dry-run
rmos sync
```

## Current status

This repo is a safe scaffold, not a production-ready reMarkable modification. The initial client can discover marked UUIDs and pull their raw xochitl bundles. Rendering handwriting to PDF/PNG and the final xochitl UI button are explicitly staged as follow-up work.

See `SPEC.md` and `docs/ARCHITECTURE.md`.
