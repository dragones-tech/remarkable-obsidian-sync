# reMarkable Sync — Omarchy plugin

A bar icon that appears while a reMarkable is plugged in, showing which tagged
notebooks have not reached your Obsidian vault yet, with a picker for choosing
what syncs.

It is a thin shell over [`rmos`](../README.md); all the sync logic lives there.

## Status

Phase B: the manifest and the `bin/` scripts are done and tested. The QML
entry points are placeholders — the bar widget and the picker come next.

## How it is put together

Omarchy plugins are QML on Quickshell plus scripts in `bin/`. The scripts print
**one line of JSON** on stdout; the QML runs them with `Process` and parses
that. Every script here holds to that, including when it fails: an expected
problem — no tablet, no `rmos` — comes back as an `error` field with exit 0, so
the widget can say what is wrong instead of showing nothing.

| Script | What it does | Cost |
| --- | --- | --- |
| `rmos-probe` | Is the tablet plugged in? | A walk of `/sys`. No `ip`, no ping, no SSH. |
| `rmos-report` | What is pending | One SSH round trip per selected notebook |
| `rmos-catalog` | Every notebook, folder, tags, selection state | Reads the tablet's document index |
| `rmos-run` | Sync now | A transfer per changed notebook |
| `rmos-apply` | Persist what the picker chose | Cheap |
| `rmos-pair` | Install an SSH key using the tablet's password | Once |
| `rmos-unpair` | Revoke it | Once |
| `rmos-open` | Open the synced folder in Obsidian | Cheap |

`rmos-probe` is the one that matters for feel. The bar icon exists **only while
the tablet is connected**, so it is polled on a timer — which is why it must
cost nothing to ask, and why it never touches the network.

## Pairing, and why there is no stored password

Sync-on-attach is triggered by udev and has no terminal, so it can never answer
a password prompt. Key authentication is not a nicety here; it is what makes
unattended sync possible at all.

So the password is a **pairing** field, not a stored credential:

```bash
printf '%s' 'the-tablet-password' | bin/rmos-pair
```

It arrives on stdin — never as an argument, which `ps` would expose — is used
once to copy a key across, and is then gone. Nothing writes it to disk. The key
is a dedicated `~/.ssh/rmos_remarkable`, wired up through a fenced block in
`~/.ssh/config` so `rmos` needs no extra configuration:

```
# >>> rmos (remarkable-sync) >>>
Host 10.11.99.1
    IdentityFile ~/.ssh/rmos_remarkable
    IdentitiesOnly yes
# <<< rmos (remarkable-sync) <<<
```

`bin/rmos-unpair` removes the key from the tablet and that block from your ssh
config, leaving everything around it alone.

## Configuration

`~/.config/omarchy/remarkable-sync.json`:

```json
{
  "rmosPath": "",
  "host": "10.11.99.1",
  "user": "root",
  "vendor": "04b3",
  "product": "4010",
  "pollSeconds": 4,
  "autoSync": false
}
```

`vendor`/`product` are the tablet's USB gadget ids, which is how the probe
recognises it. `04b3:4010` is what a reMarkable running firmware
`20260612085811` presents; `lsusb` will tell you if yours differs.

Everything about *what* syncs and *where* lives in the `rmos` config instead,
and the picker writes it through `rmos config set`.

## Install

```bash
./install.sh          # copy into ~/.config/omarchy/plugins/
./install.sh --link   # symlink instead, for development
omarchy plugin enable io.github.dragones-tech.remarkable-sync
./uninstall.sh
```

Publishing it properly means `omarchy plugin add <git-url>`, which clones a
repo and expects `manifest.json` at its root — so that needs this directory to
become its own repository.
