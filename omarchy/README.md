# reMarkable Sync — Omarchy plugin

A bar icon that appears while a reMarkable is plugged in, showing which tagged
notebooks have not reached your Obsidian vault yet, with a picker for choosing
what syncs.

It is a thin shell over [`rmos`](../README.md); all the sync logic lives there.

## Status

Phase D: the bar widget and the picker are both written and installed.

## Editing the QML

`omarchy plugin rescanPlugins` does **not** rebuild a component that is
already loaded — an edited QML file will appear to change nothing. Use:

```bash
omarchy-restart-shell
```

Do not use `omarchy refresh shell`: that resets `shell.json` to Omarchy's
defaults, which removes this plugin from your bar along with anything else you
have arranged there.

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
| `rmos-open` | Open a note (`--path`) or the synced folder in Obsidian | Cheap |

## The bar widget

The icon appears while the tablet is plugged in and is coloured only when
there is something to act on: notebooks waiting to sync, or a tablet that is
not paired yet. Left-click opens the popout, right-click syncs straight away —
the plug in, sync, unplug loop is most of the use.

In the popout: `Enter` or a click opens the highlighted notebook's note in
Obsidian, `s` sync, `t` choose what syncs, `o` open the vault folder, `r`
refresh. Arrow keys move the cursor.

A notebook that has not been synced yet has nothing to open and says so rather
than opening the wrong thing. The note's location arrives with the report, so
clicking costs no round trip.

The expensive call (`rmos-report`, an SSH round trip per selected notebook)
runs when the tablet appears and when the popout opens — never on the poll
timer, which only ever runs `rmos-probe`.

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

`rmos-pair --check` asks the question that matters — *will rmos connect?* — by
connecting the way rmos does, without naming a key. Testing with an explicit
`-i` would report success while rmos itself still failed, because rmos finds
the key through `~/.ssh/config`. If a key is already on the tablet but ssh is
not being told to use it, pairing adopts it and needs no password.

## The picker

Opened with `t` from the bar popout. Two ways to mark a notebook, because the
tablet offers two and neither is wrong:

- **Tags** — every notebook carrying a ticked tag syncs, and it keeps working
  as you tag more on the tablet.
- **Notebooks** — picked individually, filtered with `/`.

They are unioned, so unticking a tag never un-picks a notebook you chose by
hand. A notebook already covered by a ticked tag says so and cannot be
unticked here; untick the tag instead. Only the difference is written on save,
so nothing is re-selected for no reason.

`Enter` saves, `Tab` moves between sections, `Esc` cancels.

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
