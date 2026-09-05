from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def validate_uuid(value: str) -> str:
    value = value.strip()
    if not UUID_RE.fullmatch(value):
        raise ValueError(f"Invalid reMarkable UUID: {value!r}")
    return value.lower()


def parse_selected(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        uuid = validate_uuid(line)
        if uuid not in seen:
            out.append(uuid)
            seen.add(uuid)
    return out


def parse_metadata(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("metadata must be a JSON object")
    return data


def safe_name(name: str, fallback: str) -> str:
    cleaned = "".join("_" if ch in '/\\:*?\"<>|' else ch for ch in name).strip().strip(".")
    return cleaned or fallback


def fingerprint_tree(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.as_posix()):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        h.update(len(rel).to_bytes(4, "big"))
        h.update(rel)
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    return h.hexdigest()


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


def render_markdown(notebook: Notebook, fingerprint: str, has_pdf: bool) -> str:
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
    if has_pdf:
        lines += [f"![[attachments/{safe_name(notebook.visible_name, notebook.uuid)}.pdf]]", ""]
    else:
        lines += ["> Raw reMarkable notebook data is synchronized. Visual rendering is not enabled yet.", ""]
    return "\n".join(lines)
