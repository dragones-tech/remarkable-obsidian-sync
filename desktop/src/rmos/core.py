"""Pure helpers for the reMarkable -> Obsidian sync client.

Everything here is deliberately free of SSH and subprocess concerns so it can
be unit tested without a device. The only I/O is against the local filesystem.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# `sha256sum`/`md5sum` print "<hex><space><space-or-asterisk><path>" on both
# coreutils and busybox. The path is kept verbatim so names with spaces survive.
HASH_LINE_RE = re.compile(r"^([0-9a-fA-F]{16,}) [ *](.+)$")

SUPPORTED_ALGOS = ("sha256", "md5")

FINGERPRINT_VERSION = b"rmos-fp-1"


def validate_uuid(value: str) -> str:
    value = value.strip()
    if not UUID_RE.fullmatch(value):
        raise ValueError(f"Invalid reMarkable UUID: {value!r}")
    return value.lower()


class Selection(NamedTuple):
    """Result of reading the device-side `selected.txt` contract."""

    uuids: list[str]
    invalid: list[str]


def parse_selected(text: str) -> Selection:
    """Parse `selected.txt`, keeping malformed lines rather than aborting.

    One bad line must not prevent every other notebook from syncing, so
    offending lines are returned for the caller to report.
    """
    uuids: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            uuid = validate_uuid(line)
        except ValueError:
            invalid.append(line)
            continue
        if uuid not in seen:
            uuids.append(uuid)
            seen.add(uuid)
    return Selection(uuids, invalid)


def parse_metadata(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("metadata must be a JSON object")
    return data


def safe_name(name: str, fallback: str) -> str:
    cleaned = "".join("_" if ch in '/\\:*?"<>|' else ch for ch in name).strip().strip(".")
    return cleaned or fallback


def parse_hash_listing(text: str) -> list[tuple[str, str]]:
    """Turn `sha256sum`/`md5sum` output into (path, digest) pairs."""
    entries: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        match = HASH_LINE_RE.match(line)
        if not match:
            raise ValueError(f"Unparseable checksum line: {line!r}")
        digest, path = match.group(1), match.group(2)
        entries.append((path, digest.lower()))
    return entries


def fingerprint_entries(entries: Iterable[tuple[str, str]], *, algo: str) -> str:
    """Fold (path, digest) pairs into one order-independent fingerprint.

    The algorithm name is mixed in, so a fingerprint taken with md5 per-file
    digests can never compare equal to one taken with sha256.
    """
    h = hashlib.sha256()
    h.update(FINGERPRINT_VERSION + b"\0")
    h.update(algo.encode("utf-8") + b"\0")
    for path, digest in sorted(entries):
        h.update(path.encode("utf-8") + b"\0")
        h.update(digest.lower().encode("utf-8") + b"\0")
    return h.hexdigest()


def file_digest(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_entries(root: Path) -> list[tuple[str, Path]]:
    """Regular files under `root`, as (relative posix path, absolute path)."""
    out: list[tuple[str, Path]] = []
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        if path.is_symlink() or not path.is_file():
            continue
        out.append((path.relative_to(root).as_posix(), path))
    return out


def fingerprint_tree(root: Path, *, algo: str = "sha256") -> str:
    return fingerprint_entries(
        ((rel, file_digest(path, algo)) for rel, path in tree_entries(root)),
        algo=algo,
    )


def find_symlinks(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_symlink()]


@dataclass(frozen=True)
class Notebook:
    uuid: str
    visible_name: str
    last_modified: str | None = None


def notebook_from_metadata(uuid: str, metadata: dict) -> Notebook:
    visible = metadata.get("visibleName")
    if not isinstance(visible, str) or not visible.strip():
        visible = uuid
    modified = metadata.get("lastModified")
    if modified is not None:
        modified = str(modified)
    return Notebook(uuid=validate_uuid(uuid), visible_name=visible.strip(), last_modified=modified)


def render_markdown(notebook: Notebook, fingerprint: str, *, pdf_name: str | None = None) -> str:
    """Render the vault note.

    Deterministic on purpose: identical notebook content must produce a
    byte-identical file so an unchanged sync never dirties the vault.
    """
    lines = [
        "---",
        "source: remarkable",
        f"remarkable_id: {notebook.uuid}",
        f"remarkable_modified: {notebook.last_modified or ''}",
        f"rmos_fingerprint: {fingerprint}",
        "---",
        "",
        f"# {notebook.visible_name}",
        "",
    ]
    if pdf_name:
        lines += [f"![[attachments/{pdf_name}]]", ""]
    else:
        lines += ["> Raw reMarkable notebook data is synchronized. Visual rendering is not enabled yet.", ""]
    return "\n".join(lines)


FRONTMATTER_ID_RE = re.compile(r"^remarkable_id:\s*(\S+)\s*$", re.MULTILINE)


def frontmatter_id(text: str) -> str | None:
    """Read `remarkable_id` out of a note's YAML frontmatter block."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    match = FRONTMATTER_ID_RE.search(text[:end])
    return match.group(1) if match else None


def destination_owner(dest: Path) -> str | None:
    """UUID of the notebook an existing vault folder already belongs to."""
    if not dest.is_dir():
        return None
    for md in sorted(dest.glob("*.md")):
        try:
            owner = frontmatter_id(md.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if owner:
            return owner
    return None


def destination_conflicts(dest: Path, uuid: str) -> bool:
    """True when writing into `dest` would clobber someone else's content."""
    if not dest.exists():
        return False
    if not dest.is_dir():
        return True
    owner = destination_owner(dest)
    if owner is not None:
        return owner != uuid
    # No rmos note inside: only a name clash with a note we would overwrite matters.
    return (dest / f"{dest.name}.md").exists()


def plan_destination(source: Path, documents: dict, uuid: str, visible_name: str) -> Path:
    """Pick the vault folder for a notebook, disambiguating name collisions.

    Identity is the UUID, never the name, so the same notebook always resolves
    to the same folder for as long as its name is unchanged, and two notebooks
    sharing a visible name never resolve to the same folder.
    """
    base = safe_name(visible_name, uuid)
    taken = {
        Path(entry["destination"]).name
        for other, entry in documents.items()
        if other != uuid and isinstance(entry, dict) and entry.get("destination")
    }
    candidate = source / base
    if base not in taken and not destination_conflicts(candidate, uuid):
        return candidate
    return source / f"{base} ({uuid[:8]})"


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".rmos-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def replace_tree(source: Path, dest: Path) -> None:
    """Swap `dest` for a copy of `source`, minimising the window with no data."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    staged = dest.with_name(dest.name + ".rmos-new")
    retired = dest.with_name(dest.name + ".rmos-old")
    for leftover in (staged, retired):
        if leftover.exists():
            shutil.rmtree(leftover)
    shutil.copytree(source, staged)
    if dest.exists():
        os.replace(dest, retired)
    os.replace(staged, dest)
    if retired.exists():
        shutil.rmtree(retired)


def prune_stale_notes(dest: Path, uuid: str, keep: str) -> list[Path]:
    """Delete notes in `dest` we previously generated under an older name.

    Only files whose frontmatter claims this exact UUID are removed, so a
    user's own notes living beside ours are never touched.
    """
    removed: list[Path] = []
    for md in sorted(dest.glob("*.md")):
        if md.name == keep:
            continue
        try:
            owner = frontmatter_id(md.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if owner == uuid:
            md.unlink()
            removed.append(md)
    return removed
