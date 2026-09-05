from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from .core import fingerprint_tree, notebook_from_metadata, parse_metadata, parse_selected, render_markdown, safe_name

REMOTE_XOCHITL = "/home/root/.local/share/remarkable/xochitl"
REMOTE_SELECTED = "/home/root/.local/share/rmos/selected.txt"


def load_config(path: Path | None) -> dict:
    path = path or Path("~/.config/rmos/config.toml").expanduser()
    with path.open("rb") as f:
        return tomllib.load(f)


def remote(cfg: dict) -> str:
    r = cfg["remarkable"]
    return f"{r.get('user', 'root')}@{r.get('host', '10.11.99.1')}"


def ssh_base(cfg: dict) -> list[str]:
    opts = cfg.get("remarkable", {}).get("ssh_options", [])
    return ["ssh", *opts, remote(cfg)]


def run_ssh(cfg: dict, command: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*ssh_base(cfg), command], text=True, capture_output=True, check=check)


def read_remote(cfg: dict, path: str) -> str:
    # Path is an internal constant/validated UUID-derived path, never arbitrary shell input.
    cp = run_ssh(cfg, f"cat -- '{path}'")
    return cp.stdout


def selected(cfg: dict) -> list[str]:
    cp = run_ssh(cfg, f"test -f '{REMOTE_SELECTED}' && cat -- '{REMOTE_SELECTED}' || true")
    return parse_selected(cp.stdout)


def metadata_for(cfg: dict, uuid: str) -> dict:
    return parse_metadata(read_remote(cfg, f"{REMOTE_XOCHITL}/{uuid}.metadata"))


def copy_remote_bundle(cfg: dict, uuid: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    # Enumerate only files whose basename starts with the validated UUID.
    command = (
        f"cd '{REMOTE_XOCHITL}' && "
        f"for p in '{uuid}' '{uuid}'.*; do [ -e \"$p\" ] && printf '%s\\n' \"$p\"; done"
    )
    cp = run_ssh(cfg, command)
    names = [line.strip() for line in cp.stdout.splitlines() if line.strip()]
    if not names:
        raise RuntimeError(f"No document bundle found for {uuid}")
    for name in names:
        if not (name == uuid or name.startswith(uuid + ".")):
            raise RuntimeError(f"Unexpected remote bundle member: {name}")
        subprocess.run(["scp", "-r", f"{remote(cfg)}:{REMOTE_XOCHITL}/{name}", str(dest / name)], check=True)


def state_path(cfg: dict) -> Path:
    return Path(cfg.get("rmos", {}).get("state", "~/.local/state/rmos/state.json")).expanduser()


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"documents": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def vault_source(cfg: dict) -> Path:
    vault = Path(cfg["obsidian"]["vault"]).expanduser()
    return vault / cfg["obsidian"].get("source", "Sources/reMarkable")


def cmd_doctor(cfg: dict) -> int:
    print(f"remote: {remote(cfg)}")
    cp = run_ssh(cfg, f"test -d '{REMOTE_XOCHITL}' && echo xochitl-ok; test -f '{REMOTE_SELECTED}' && echo selection-ok || echo selection-missing", check=False)
    if cp.returncode != 0:
        print(cp.stderr.strip(), file=sys.stderr)
        return 1
    print(cp.stdout.strip())
    print(f"vault source: {vault_source(cfg)}")
    return 0


def cmd_list(cfg: dict) -> int:
    uuids = selected(cfg)
    if not uuids:
        print("No notebooks selected.")
        return 0
    for uuid in uuids:
        nb = notebook_from_metadata(uuid, metadata_for(cfg, uuid))
        print(f"{nb.uuid}\t{nb.visible_name}")
    return 0


def cmd_sync(cfg: dict, dry_run: bool) -> int:
    source = vault_source(cfg)
    s_path = state_path(cfg)
    state = load_state(s_path)
    docs = state.setdefault("documents", {})

    for uuid in selected(cfg):
        nb = notebook_from_metadata(uuid, metadata_for(cfg, uuid))
        with tempfile.TemporaryDirectory(prefix="rmos-") as td:
            temp_root = Path(td) / "raw"
            copy_remote_bundle(cfg, uuid, temp_root)
            fp = fingerprint_tree(temp_root)
            previous = docs.get(uuid, {})
            if previous.get("fingerprint") == fp:
                print(f"skip unchanged: {nb.visible_name}")
                continue

            folder_name = safe_name(nb.visible_name, uuid)
            dest = source / folder_name
            print(f"{'would sync' if dry_run else 'sync'}: {nb.visible_name} -> {dest}")
            if dry_run:
                continue

            dest.mkdir(parents=True, exist_ok=True)
            raw_dest = dest / "raw"
            replacement = dest / ".raw.new"
            if replacement.exists():
                shutil.rmtree(replacement)
            shutil.copytree(temp_root, replacement)
            if raw_dest.exists():
                shutil.rmtree(raw_dest)
            os.replace(replacement, raw_dest)

            md = render_markdown(nb, fp, has_pdf=False)
            (dest / f"{folder_name}.md").write_text(md, encoding="utf-8")

            docs[uuid] = {
                "fingerprint": fp,
                "visible_name": nb.visible_name,
                "destination": str(dest),
                "synced_at": datetime.now(timezone.utc).isoformat(),
            }
            save_state(s_path, state)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rmos")
    p.add_argument("--config", type=Path)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("list")
    sync = sub.add_parser("sync")
    sync.add_argument("--dry-run", action="store_true")
    return p


def main() -> None:
    args = build_parser().parse_args()
    try:
        cfg = load_config(args.config)
        if args.command == "doctor":
            code = cmd_doctor(cfg)
        elif args.command == "list":
            code = cmd_list(cfg)
        else:
            code = cmd_sync(cfg, args.dry_run)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
