"""Command line client: pulls selected reMarkable notebooks into an Obsidian vault.

The device is treated as read-only except for our own state directory under
/home/root/.local/share/rmos/. Nothing here ever writes into xochitl.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .core import (
    Notebook,
    find_symlinks,
    fingerprint_entries,
    fingerprint_tree,
    notebook_from_metadata,
    parse_hash_listing,
    parse_metadata,
    parse_selected,
    plan_destination,
    prune_stale_notes,
    render_markdown,
    replace_tree,
    validate_uuid,
    write_text_atomic,
)
from .render import BundleReport, CommandRenderer, Renderer, RenderError, build_renderer, inspect_bundle

REMOTE_XOCHITL = "/home/root/.local/share/remarkable/xochitl"
REMOTE_STATE_DIR = "/home/root/.local/share/rmos"
REMOTE_SELECTED = f"{REMOTE_STATE_DIR}/selected.txt"

DEFAULT_CONFIG_PATH = Path("~/.config/rmos/config.toml")
DEFAULT_STATE_PATH = "~/.local/state/rmos/state.json"

# Exit codes used by the remote shell snippets, so failures are diagnosable.
RC_NO_XOCHITL = 3
RC_NO_BUNDLE = 4

HASH_COMMANDS = {"sha256": "sha256sum", "md5": "md5sum"}


class RmosError(RuntimeError):
    """A failure we can explain to the user without a traceback."""


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    host: str = "10.11.99.1"
    user: str = "root"
    ssh_options: list[str] = field(default_factory=list)
    multiplex: bool = True
    connect_timeout: int = 10
    vault: Path = Path()
    source: str = "Sources/reMarkable"
    state: Path = field(default_factory=lambda: Path(DEFAULT_STATE_PATH).expanduser())
    render: dict = field(default_factory=dict)

    @property
    def remote(self) -> str:
        return f"{self.user}@{self.host}"

    @property
    def vault_source(self) -> Path:
        return self.vault / self.source


def load_config(path: Path | None) -> Config:
    path = (path or DEFAULT_CONFIG_PATH).expanduser()
    if not path.exists():
        raise RmosError(f"No config at {path}. Run `rmos init-config` to create one.")
    with path.open("rb") as f:
        raw = tomllib.load(f)

    rm = raw.get("remarkable", {})
    ob = raw.get("obsidian", {})
    st = raw.get("rmos", {})

    vault = ob.get("vault")
    if not vault:
        raise RmosError(f"{path}: [obsidian] vault is required.")

    return Config(
        host=rm.get("host", "10.11.99.1"),
        user=rm.get("user", "root"),
        ssh_options=list(rm.get("ssh_options", [])),
        multiplex=bool(rm.get("multiplex", True)),
        connect_timeout=int(rm.get("connect_timeout", 10)),
        vault=Path(vault).expanduser(),
        source=ob.get("source", "Sources/reMarkable"),
        state=Path(st.get("state", DEFAULT_STATE_PATH)).expanduser(),
        render=dict(raw.get("render", {})),
    )


def build_configured_renderer(cfg: Config) -> Renderer:
    try:
        return build_renderer(cfg.render)
    except ValueError as exc:
        raise RmosError(str(exc)) from exc


CONFIG_TEMPLATE = """\
[remarkable]
host = "10.11.99.1"
user = "root"
# Extra flags passed to ssh, e.g. ["-i", "~/.ssh/remarkable"].
ssh_options = []
# Reuse one SSH connection for the whole run, so password auth prompts once.
multiplex = true
connect_timeout = 10

[obsidian]
vault = "{vault}"
source = "Sources/reMarkable"

[rmos]
state = "{state}"

# Rendering is off until you have confirmed which stroke format your firmware
# writes. Run `rmos inspect` after a sync to find out, then point `command` at
# a tool that supports that version. Placeholders: {{raw}} {{uuid}} {{name}} {{out}}.
[render]
backend = "none"
# backend = "command"
# command = ["my-renderer", "--input", "{{raw}}", "--output", "{{out}}"]
# extension = "pdf"
# timeout = 300
"""


# --------------------------------------------------------------------------
# SSH transport
# --------------------------------------------------------------------------


class Ssh:
    """Runs commands on the tablet over one multiplexed SSH connection."""

    def __init__(self, cfg: Config, *, verbose: bool = False, batch: bool = False) -> None:
        self.cfg = cfg
        self.verbose = verbose
        # Unattended runs have no terminal, so ssh must fail rather than block
        # on a password or host-key prompt nobody can answer.
        self.batch = batch
        self._control_path: str | None = None
        self._hash_algo: str | None = None

    def _control_args(self) -> list[str]:
        if not self.cfg.multiplex:
            return []
        if self._control_path is None:
            runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
            socket_dir = Path(runtime) / "rmos"
            socket_dir.mkdir(parents=True, exist_ok=True)
            self._control_path = str(socket_dir / "cm-%C")
        return [
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={self._control_path}",
            "-o", "ControlPersist=60",
        ]

    def args(self) -> list[str]:
        batch = ["-o", "BatchMode=yes"] if self.batch else []
        return [
            "ssh",
            "-o", f"ConnectTimeout={self.cfg.connect_timeout}",
            *batch,
            *self._control_args(),
            *self.cfg.ssh_options,
            self.cfg.remote,
        ]

    def reachable(self) -> bool:
        cp = self.run("echo rmos-ok", check=False)
        return cp.returncode == 0 and "rmos-ok" in cp.stdout

    def wait_for_device(self, seconds: int) -> None:
        """Block until the tablet answers, or give up after `seconds`.

        A USB network interface appears before the tablet's sshd is ready to
        accept connections, so an attach-triggered sync that connects once
        would usually just miss it.
        """
        deadline = time.monotonic() + seconds
        delay = 1.0
        while True:
            if self.reachable():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RmosError(f"Tablet not reachable at {self.cfg.remote} after {seconds}s.")
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, 5.0)

    def run(self, script: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        argv = [*self.args(), script]
        if self.verbose:
            print(f"+ ssh {self.cfg.remote} <<script>>", file=sys.stderr)
        return subprocess.run(argv, text=True, capture_output=True, check=check)

    def output(self, script: str) -> str:
        cp = self.run(script, check=False)
        if cp.returncode != 0:
            raise RmosError(_ssh_failure(cp))
        return cp.stdout

    @property
    def hash_algo(self) -> str:
        """Which checksum tool the tablet actually has, resolved once."""
        if self._hash_algo is None:
            probe = (
                "if command -v sha256sum >/dev/null 2>&1; then echo sha256; "
                "elif command -v md5sum >/dev/null 2>&1; then echo md5; "
                "else echo none; fi"
            )
            found = self.output(probe).strip()
            if found not in HASH_COMMANDS:
                raise RmosError("Neither sha256sum nor md5sum is available on the tablet.")
            self._hash_algo = found
        return self._hash_algo


def make_ssh(cfg: Config, args: argparse.Namespace) -> Ssh:
    ssh = Ssh(cfg, verbose=args.verbose, batch=args.batch)
    wait = getattr(args, "wait", 0)
    if wait:
        ssh.wait_for_device(wait)
    return ssh


def _ssh_failure(cp: subprocess.CompletedProcess[str]) -> str:
    if cp.returncode == RC_NO_XOCHITL:
        return f"Remote directory not found: {REMOTE_XOCHITL}"
    if cp.returncode == RC_NO_BUNDLE:
        return "No document bundle found on the tablet for that UUID."
    detail = cp.stderr.strip() or cp.stdout.strip() or "no output"
    return f"SSH command failed (exit {cp.returncode}): {detail}"


def _bundle_preamble(uuid: str) -> str:
    """Shell that positions `$@` over exactly one notebook's bundle members.

    `uuid` is validated hex before it reaches here, so single-quoting it is
    sufficient; nothing user-controlled is interpolated.
    """
    return (
        f"cd '{REMOTE_XOCHITL}' 2>/dev/null || exit {RC_NO_XOCHITL}\n"
        "set --\n"
        f"for p in '{uuid}' '{uuid}'.*; do\n"
        '  if [ -e "$p" ]; then set -- "$@" "$p"; fi\n'
        "done\n"
        f'if [ "$#" -eq 0 ]; then exit {RC_NO_BUNDLE}; fi\n'
    )


def remote_metadata(ssh: Ssh, uuid: str) -> dict:
    script = f"cd '{REMOTE_XOCHITL}' 2>/dev/null || exit {RC_NO_XOCHITL}\ncat '{uuid}.metadata'"
    cp = ssh.run(script, check=False)
    if cp.returncode != 0:
        if cp.returncode not in (RC_NO_XOCHITL,):
            raise RmosError(f"Cannot read metadata for {uuid}: {cp.stderr.strip() or 'not found'}")
        raise RmosError(_ssh_failure(cp))
    return parse_metadata(cp.stdout)


def remote_fingerprint(ssh: Ssh, uuid: str) -> tuple[str, list[str]]:
    """Fingerprint a bundle without transferring it. Returns (fingerprint, members)."""
    hasher = HASH_COMMANDS[ssh.hash_algo]
    script = _bundle_preamble(uuid) + (
        'find "$@" -type f | LC_ALL=C sort | while IFS= read -r f; do ' + hasher + ' "$f"; done\n'
    )
    entries = parse_hash_listing(ssh.output(script))
    if not entries:
        raise RmosError(f"Bundle for {uuid} contains no files.")
    for path, _ in entries:
        _reject_unexpected_member(path, uuid)
    return fingerprint_entries(entries, algo=ssh.hash_algo), [p for p, _ in entries]


def _reject_unexpected_member(path: str, uuid: str) -> None:
    if path != uuid and not path.startswith(uuid + "/") and not path.startswith(uuid + "."):
        raise RmosError(f"Refusing unexpected bundle member outside {uuid}: {path!r}")


def pull_bundle(ssh: Ssh, uuid: str, dest: Path) -> None:
    """Stream one bundle over a single SSH connection via tar.

    Using tar instead of per-file scp keeps this to one connection (one
    password prompt) and avoids depending on sftp-server, which modern scp
    requires but the tablet does not necessarily provide.
    """
    dest.mkdir(parents=True, exist_ok=True)
    script = _bundle_preamble(uuid) + 'tar -cf - "$@"\n'

    with tempfile.TemporaryFile(mode="w+") as errfile:
        ssh_proc = subprocess.Popen([*ssh.args(), script], stdout=subprocess.PIPE, stderr=errfile)
        assert ssh_proc.stdout is not None
        tar_proc = subprocess.Popen(
            ["tar", "-xf", "-", "--no-same-owner", "-C", str(dest)],
            stdin=ssh_proc.stdout,
            stderr=subprocess.PIPE,
            text=True,
        )
        ssh_proc.stdout.close()
        tar_err = tar_proc.communicate()[1]
        ssh_rc = ssh_proc.wait()
        errfile.seek(0)
        ssh_err = errfile.read().strip()

    if ssh_rc == RC_NO_XOCHITL:
        raise RmosError(f"Remote directory not found: {REMOTE_XOCHITL}")
    if ssh_rc == RC_NO_BUNDLE:
        raise RmosError(f"No document bundle found on the tablet for {uuid}.")
    if ssh_rc != 0:
        raise RmosError(f"Transfer failed (ssh exit {ssh_rc}): {ssh_err or 'no output'}")
    if tar_proc.returncode != 0:
        raise RmosError(f"Extraction failed (tar exit {tar_proc.returncode}): {(tar_err or '').strip()}")

    stray = find_symlinks(dest)
    if stray:
        raise RmosError(f"Refusing bundle containing symlinks: {stray[0]}")


# --------------------------------------------------------------------------
# Local state
# --------------------------------------------------------------------------


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"documents": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RmosError(f"Corrupt state file {path}: {exc}") from exc
    state.setdefault("documents", {})
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_selection(ssh: Ssh) -> list[str]:
    script = f"if [ -f '{REMOTE_SELECTED}' ]; then cat '{REMOTE_SELECTED}'; fi"
    selection = parse_selected(ssh.output(script))
    for bad in selection.invalid:
        print(f"warning: ignoring malformed line in selected.txt: {bad!r}", file=sys.stderr)
    return selection.uuids


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fatal: bool = True


def _writable_ancestor(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def cmd_doctor(cfg: Config, args: argparse.Namespace) -> int:
    ssh = Ssh(cfg, verbose=args.verbose, batch=args.batch)
    checks: list[Check] = []

    for tool in ("ssh", "tar"):
        found = shutil.which(tool)
        checks.append(Check(f"local `{tool}`", bool(found), found or "not found on PATH"))

    reachable = False
    if all(c.ok for c in checks):
        cp = ssh.run("echo rmos-ok", check=False)
        reachable = cp.returncode == 0 and "rmos-ok" in cp.stdout
        checks.append(Check(f"ssh {cfg.remote}", reachable, "" if reachable else _ssh_failure(cp)))

    if reachable:
        probe = (
            f"[ -d '{REMOTE_XOCHITL}' ] && echo xochitl; "
            f"[ -f '{REMOTE_SELECTED}' ] && echo selection; "
            "command -v tar >/dev/null 2>&1 && echo tar; "
            "command -v sha256sum >/dev/null 2>&1 && echo sha256sum; "
            "command -v md5sum >/dev/null 2>&1 && echo md5sum; "
            "true"
        )
        have = set(ssh.output(probe).split())
        checks.append(Check("remote xochitl directory", "xochitl" in have, REMOTE_XOCHITL))
        checks.append(Check("remote tar", "tar" in have))
        checks.append(
            Check(
                "remote checksum tool",
                bool(have & {"sha256sum", "md5sum"}),
                "sha256sum" if "sha256sum" in have else ("md5sum" if "md5sum" in have else "none"),
            )
        )
        checks.append(
            Check(
                "remote selected.txt",
                "selection" in have,
                REMOTE_SELECTED if "selection" in have else "missing; run remarkable/install.sh or `rmos select`",
                fatal=False,
            )
        )

    if reachable:
        # Must bypass the multiplexed socket: it is already authenticated, so
        # reusing it would report key auth as working when it is not.
        probe = Ssh(dataclasses.replace(cfg, multiplex=False), verbose=args.verbose, batch=True)
        unattended = probe.reachable()
        checks.append(
            Check(
                "unattended-ready",
                unattended,
                "key authentication works"
                if unattended
                else f"ssh needs a password; run `ssh-copy-id {cfg.remote}` to enable sync on USB attach",
                fatal=False,
            )
        )

    try:
        renderer = build_configured_renderer(cfg)
        detail = renderer.name
        if isinstance(renderer, CommandRenderer):
            executable = renderer.command[0]
            found = shutil.which(executable)
            checks.append(Check("render command", bool(found), found or f"{executable} not found on PATH"))
        checks.append(Check("render backend", True, detail, fatal=False))
    except RmosError as exc:
        checks.append(Check("render backend", False, str(exc)))

    vault_ok = cfg.vault.is_dir()
    checks.append(Check("obsidian vault", vault_ok, str(cfg.vault) if vault_ok else f"{cfg.vault} is not a directory"))
    if vault_ok:
        anchor = _writable_ancestor(cfg.vault_source)
        writable = os.access(anchor, os.W_OK)
        checks.append(Check("vault writable", writable, str(cfg.vault_source)))

    failed = 0
    for check in checks:
        if check.ok:
            mark = "ok  "
        elif check.fatal:
            mark = "FAIL"
            failed += 1
        else:
            mark = "warn"
        suffix = f"  ({check.detail})" if check.detail else ""
        print(f"[{mark}] {check.name}{suffix}")
    return 1 if failed else 0


def cmd_list(cfg: Config, args: argparse.Namespace) -> int:
    ssh = make_ssh(cfg, args)
    uuids = read_selection(ssh)
    if not uuids:
        print("No notebooks selected.")
        return 0
    failed = 0
    for uuid in uuids:
        try:
            nb = notebook_from_metadata(uuid, remote_metadata(ssh, uuid))
            print(f"{nb.uuid}\t{nb.visible_name}")
        except (RmosError, ValueError) as exc:
            print(f"{uuid}\t<error: {exc}>", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


def cmd_status(cfg: Config, args: argparse.Namespace) -> int:
    ssh = make_ssh(cfg, args)
    state = load_state(cfg.state)
    docs = state["documents"]
    uuids = read_selection(ssh)

    print(f"remote:      {cfg.remote}")
    print(f"vault:       {cfg.vault_source}")
    print(f"state:       {cfg.state}")
    print(f"selected:    {len(uuids)}")
    print(f"tracked:     {len(docs)}")
    if uuids:
        print()

    failed = 0
    for uuid in uuids:
        entry = docs.get(uuid, {})
        try:
            nb = notebook_from_metadata(uuid, remote_metadata(ssh, uuid))
            fingerprint, _ = remote_fingerprint(ssh, uuid)
            status = "unchanged" if entry.get("fingerprint") == fingerprint else ("new" if not entry else "changed")
            print(f"{status:<10} {nb.visible_name}  ({uuid})")
        except (RmosError, ValueError) as exc:
            print(f"{'error':<10} {uuid}  ({exc})", file=sys.stderr)
            failed += 1

    untracked = [u for u in docs if u not in uuids]
    if untracked:
        print()
        print(f"{len(untracked)} notebook(s) previously synced but no longer selected (kept in the vault):")
        for uuid in untracked:
            print(f"  {docs[uuid].get('visible_name', uuid)}  ({uuid})")
    return 1 if failed else 0


def _render_attachment(
    renderer: Renderer,
    dest: Path,
    nb: Notebook,
    previous_attachment: str | None,
) -> tuple[str | None, str]:
    """Render the notebook, returning (attachment filename, state marker).

    A renderer failure is not fatal: the raw bundle is already imported, so we
    warn, keep whatever attachment survives from an earlier run, and record the
    failure so the next sync does not silently retry a broken command.
    """
    attachments = dest / "attachments"
    produced: Path | None = None
    marker = renderer.signature

    try:
        produced = renderer.render(
            raw=dest / "raw",
            uuid=nb.uuid,
            visible_name=nb.visible_name,
            out_dir=attachments,
        )
    except RenderError as exc:
        print(f"warning: {nb.visible_name}: {exc}", file=sys.stderr)
        marker = f"failed:{renderer.signature}"

    if produced is None:
        # Nothing rendered this run. Keep the previous embed if its file is
        # still there, so turning the renderer off does not orphan the PDF.
        if previous_attachment and (attachments / previous_attachment).is_file():
            return previous_attachment, marker
        return None, marker

    if previous_attachment and previous_attachment != produced.name:
        stale = attachments / previous_attachment
        if stale.is_file():
            stale.unlink()
            print(f"           removed stale attachment {stale.name}")
    return produced.name, marker


def _sync_one(
    ssh: Ssh,
    cfg: Config,
    uuid: str,
    docs: dict,
    renderer: Renderer,
    *,
    dry_run: bool,
    re_render: bool = False,
) -> bool:
    """Sync one notebook. Returns True when the vault was modified."""
    nb: Notebook = notebook_from_metadata(uuid, remote_metadata(ssh, uuid))
    fingerprint, _ = remote_fingerprint(ssh, uuid)
    previous = docs.get(uuid, {})

    # State written before rendering existed carries no marker; treat it as the
    # null renderer so adding this feature does not force a full re-import.
    render_marker = previous.get("render", "none")
    render_current = render_marker in (renderer.signature, f"failed:{renderer.signature}")
    content_current = previous.get("fingerprint") == fingerprint

    if not re_render and content_current and render_current:
        print(f"unchanged  {nb.visible_name}")
        return False

    dest = plan_destination(cfg.vault_source, docs, uuid, nb.visible_name)
    previous_dest = Path(previous["destination"]) if previous.get("destination") else None

    # Content is already local and only the rendering is stale: re-render from
    # the bundle we hold instead of pulling an identical copy off the tablet.
    render_only = content_current and ((previous_dest or dest) / "raw").is_dir()

    verb = "re-render" if render_only else "sync"
    action = f"would {verb}" if dry_run else verb

    if previous_dest and previous_dest != dest:
        print(f"{action}   {nb.visible_name}  (renamed: {previous_dest.name} -> {dest.name})")
    else:
        print(f"{action}   {nb.visible_name} -> {dest}")
    if dry_run:
        return False

    # Identity is the UUID: follow a rename by moving the folder we already own
    # rather than leaving a stale duplicate behind.
    if previous_dest and previous_dest != dest and previous_dest.is_dir():
        if dest.exists():
            raise RmosError(f"Cannot move {previous_dest} to {dest}: destination already exists.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(previous_dest, dest)

    if not render_only:
        with tempfile.TemporaryDirectory(prefix="rmos-") as td:
            staging = Path(td) / "raw"
            pull_bundle(ssh, uuid, staging)

            # The tablet is live: verify what arrived is what we fingerprinted.
            local = fingerprint_tree(staging, algo=ssh.hash_algo)
            if local != fingerprint:
                raise RmosError(
                    f"{nb.visible_name}: bundle changed during transfer (fingerprint mismatch); vault left untouched."
                )

            dest.mkdir(parents=True, exist_ok=True)
            replace_tree(staging, dest / "raw")

    attachment, render_marker = _render_attachment(renderer, dest, nb, previous.get("attachment"))

    note_name = f"{dest.name}.md"
    write_text_atomic(dest / note_name, render_markdown(nb, fingerprint, pdf_name=attachment))
    for removed in prune_stale_notes(dest, uuid, note_name):
        print(f"           removed stale note {removed.name}")

    docs[uuid] = {
        "fingerprint": fingerprint,
        "visible_name": nb.visible_name,
        "destination": str(dest),
        "synced_at": datetime.now(UTC).isoformat(),
        "render": render_marker,
        "attachment": attachment,
    }
    return True


def cmd_sync(cfg: Config, args: argparse.Namespace) -> int:
    ssh = make_ssh(cfg, args)
    renderer = build_configured_renderer(cfg)
    state = load_state(cfg.state)
    docs = state["documents"]
    uuids = read_selection(ssh)

    if not uuids:
        print("No notebooks selected.")
        return 0

    changed = 0
    failed = 0
    for uuid in uuids:
        try:
            if _sync_one(ssh, cfg, uuid, docs, renderer, dry_run=args.dry_run, re_render=args.re_render):
                changed += 1
                # Persist after each notebook so an interruption cannot lose
                # work already written to the vault.
                save_state(cfg.state, state)
        except (RmosError, ValueError, OSError) as exc:
            print(f"error: {uuid}: {exc}", file=sys.stderr)
            failed += 1

    summary = f"{changed} updated, {len(uuids) - changed - failed} unchanged"
    print(f"\n{summary}" + (f", {failed} failed" if failed else ""))
    return 1 if failed else 0


def _human_size(count: int) -> str:
    size = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def _print_report(name: str, dest: Path, report: BundleReport) -> None:
    print(f"{name}  ({report.uuid})")
    print(f"  folder:     {dest}")
    if report.file_type:
        print(f"  type:       {report.file_type}")
    if report.page_count is not None:
        print(f"  pages:      {report.page_count}")
    print(f"  .rm files:  {report.rm_files}")
    if report.versions:
        summary = ", ".join(
            f"{version} ({count} file{'s' if count != 1 else ''})" for version, count in sorted(report.versions.items())
        )
        print(f"  format:     {summary}")
    if report.unknown_headers:
        print(f"  unknown:    {len(report.unknown_headers)} file(s) with an unrecognised header")
        for path in report.unknown_headers[:3]:
            print(f"                {path}")
        if len(report.unknown_headers) > 3:
            print(f"                ... and {len(report.unknown_headers) - 3} more")
    print(f"  size:       {_human_size(report.total_bytes)}")
    print()


def cmd_inspect(cfg: Config, args: argparse.Namespace) -> int:
    """Report the stroke format of synced bundles.

    This is the evidence needed to choose a renderer: the `.rm` format is
    version-sensitive, and a parser built for the wrong version fails quietly.
    Reads only the local vault copy, so no tablet is required.
    """
    docs = load_state(cfg.state)["documents"]
    targets = [validate_uuid(args.uuid)] if args.uuid else list(docs)

    if not targets:
        print("Nothing synced yet. Run `rmos sync` first.")
        return 0

    seen: dict[str, int] = {}
    unrecognised = 0
    failed = 0

    for uuid in targets:
        entry = docs.get(uuid)
        if not entry:
            print(f"error: {uuid} has not been synced.", file=sys.stderr)
            failed += 1
            continue
        dest = Path(entry["destination"])
        raw = dest / "raw"
        if not raw.is_dir():
            print(f"error: {uuid}: no raw bundle at {raw}", file=sys.stderr)
            failed += 1
            continue

        report = inspect_bundle(raw, uuid)
        _print_report(entry.get("visible_name", uuid), dest, report)
        for version, count in report.versions.items():
            seen[version] = seen.get(version, 0) + count
        unrecognised += len(report.unknown_headers)

    if seen or unrecognised:
        print("Summary")
        for version, count in sorted(seen.items()):
            print(f"  {version}: {count} file(s)")
        if unrecognised:
            print(f"  unrecognised: {unrecognised} file(s)")
        print()
        if len(seen) == 1 and not unrecognised:
            version = next(iter(seen))
            print(f"Your firmware writes {version} stroke data.")
            print(f"Choose a renderer that supports {version}, then set [render] in your config.")
        elif unrecognised:
            print("Some files did not match a known header. Capture one and compare it")
            print("against the format documentation before choosing a parser.")
        else:
            print("Mixed formats present. Any renderer you choose must handle all of them.")
    return 1 if failed else 0


def cmd_select(cfg: Config, args: argparse.Namespace) -> int:
    ssh = make_ssh(cfg, args)
    uuid = validate_uuid(args.uuid)
    script = (
        "set -eu\n"
        f"mkdir -p '{REMOTE_STATE_DIR}'\n"
        f"f='{REMOTE_SELECTED}'\n"
        '[ -f "$f" ] || printf \'%s\\n\' \'# UUIDs selected for Obsidian export\' > "$f"\n'
        f'if grep -Fxq \'{uuid}\' "$f"; then echo already; else printf \'%s\\n\' \'{uuid}\' >> "$f"; echo added; fi\n'
    )
    result = ssh.output(script).strip()
    print(f"already selected: {uuid}" if result == "already" else f"selected: {uuid}")
    return 0


def cmd_unselect(cfg: Config, args: argparse.Namespace) -> int:
    ssh = make_ssh(cfg, args)
    uuid = validate_uuid(args.uuid)
    script = (
        "set -eu\n"
        f"f='{REMOTE_SELECTED}'\n"
        'if [ ! -f "$f" ]; then echo missing; exit 0; fi\n'
        't="$f.tmp.$$"\n'
        f'grep -Fvx \'{uuid}\' "$f" > "$t" || true\n'
        'mv "$t" "$f"\n'
        "echo removed\n"
    )
    result = ssh.output(script).strip()
    if result == "missing":
        print("No selection file on the tablet; nothing to do.")
    else:
        print(f"unselected: {uuid}")
    print("Note: previously synced notes are intentionally kept in the vault.")
    return 0


def cmd_init_config(cfg: Config | None, args: argparse.Namespace) -> int:
    path = (args.config or DEFAULT_CONFIG_PATH).expanduser()
    if path.exists() and not args.force:
        print(f"{path} already exists. Use --force to overwrite.", file=sys.stderr)
        return 1
    vault = str(Path(args.vault).expanduser()) if args.vault else str(Path.home() / "Documents/Obsidian/MyVault")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, CONFIG_TEMPLATE.format(vault=vault, state=DEFAULT_STATE_PATH))
    print(f"Wrote {path}")
    if not args.vault:
        print("Edit the [obsidian] vault path, then run `rmos doctor`.")
    return 0


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rmos", description=__doc__.splitlines()[0])
    p.add_argument("--config", type=Path, help=f"config file (default: {DEFAULT_CONFIG_PATH})")
    p.add_argument("-v", "--verbose", action="store_true", help="log remote commands to stderr")
    p.add_argument(
        "--batch",
        action="store_true",
        help="never prompt; fail instead. Required for unattended runs, which have no terminal",
    )
    p.add_argument(
        "--wait",
        type=int,
        default=0,
        metavar="SECONDS",
        help="wait up to SECONDS for the tablet to answer before giving up",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check the local tools, the tablet and the vault")
    sub.add_parser("list", help="list selected notebooks")
    sub.add_parser("status", help="show which selected notebooks would change")

    sync = sub.add_parser("sync", help="import selected notebooks into the vault")
    sync.add_argument("--dry-run", action="store_true", help="report changes without writing")
    sync.add_argument(
        "--re-render",
        action="store_true",
        help="re-run the renderer even for unchanged notebooks (no transfer)",
    )

    inspect = sub.add_parser("inspect", help="report the stroke format of synced notebooks")
    inspect.add_argument("uuid", nargs="?", help="one notebook; defaults to all synced ones")

    select = sub.add_parser("select", help="mark a notebook for export, from the desktop")
    select.add_argument("uuid")

    unselect = sub.add_parser("unselect", help="unmark a notebook (keeps existing vault notes)")
    unselect.add_argument("uuid")

    init = sub.add_parser("init-config", help="write a starter config file")
    init.add_argument("--vault", help="path to the Obsidian vault")
    init.add_argument("--force", action="store_true", help="overwrite an existing config")
    return p


COMMANDS = {
    "doctor": cmd_doctor,
    "list": cmd_list,
    "status": cmd_status,
    "sync": cmd_sync,
    "inspect": cmd_inspect,
    "select": cmd_select,
    "unselect": cmd_unselect,
}


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "init-config":
            code = cmd_init_config(None, args)
        else:
            code = COMMANDS[args.command](load_config(args.config), args)
    except RmosError as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 1
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
