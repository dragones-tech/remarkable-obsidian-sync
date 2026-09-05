"""Pluggable rendering of a raw reMarkable bundle into a vault attachment.

No `.rm` parser is bundled. The stroke format is version-sensitive, and a
parser written against the wrong version fails quietly or draws the wrong
thing, so choosing one is deliberately left to the user - who can point the
`command` backend at whichever tool matches the firmware they actually run.

`rmos inspect` reports which format version a synced bundle contains, which is
the evidence that decision needs.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .core import page_order, safe_name

# The first 43 bytes of a `.rm` file identify the stroke format. Matching is by
# prefix, so trailing padding differences do not matter.
RM_HEADERS: tuple[tuple[bytes, str], ...] = (
    (b"reMarkable lines with selections and layers", "v1/v2"),
    (b"reMarkable .lines file, version=3", "v3"),
    (b"reMarkable .lines file, version=5", "v5"),
    (b"reMarkable .lines file, version=6", "v6"),
)

RM_HEADER_BYTES = 64


class RenderError(RuntimeError):
    """The renderer ran but did not produce a usable attachment."""


# --------------------------------------------------------------------------
# Format inspection
# --------------------------------------------------------------------------


def detect_rm_version(path: Path) -> str | None:
    """Identify the stroke format of one `.rm` file, or None if unrecognised."""
    try:
        with path.open("rb") as f:
            head = f.read(RM_HEADER_BYTES)
    except OSError:
        return None
    for prefix, label in RM_HEADERS:
        if head.startswith(prefix):
            return label
    return None


@dataclass(frozen=True)
class BundleReport:
    """What a synced raw bundle actually contains."""

    uuid: str
    file_type: str | None
    page_count: int | None
    rm_files: int
    versions: dict[str, int]
    unknown_headers: list[str]
    total_bytes: int

    @property
    def renderable(self) -> bool:
        return self.rm_files > 0 and not self.unknown_headers


def inspect_bundle(raw: Path, uuid: str) -> BundleReport:
    """Summarise a raw bundle without interpreting stroke data.

    Everything here is derived from headers and the `.content` sidecar, so it
    stays correct regardless of which stroke format the firmware writes.
    """
    file_type: str | None = None
    page_count: int | None = None

    content = raw / f"{uuid}.content"
    if content.is_file():
        try:
            data = json.loads(content.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            if isinstance(data.get("fileType"), str):
                file_type = data["fileType"] or None
            for key in ("pageCount", "pages"):
                value = data.get(key)
                if isinstance(value, int):
                    page_count = value
                    break
                if isinstance(value, list):
                    page_count = len(value)
                    break

    versions: dict[str, int] = {}
    unknown: list[str] = []
    rm_files = 0
    total = 0

    for path in sorted(raw.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        total += path.stat().st_size
        if path.suffix != ".rm":
            continue
        rm_files += 1
        version = detect_rm_version(path)
        if version is None:
            unknown.append(path.relative_to(raw).as_posix())
        else:
            versions[version] = versions.get(version, 0) + 1

    return BundleReport(
        uuid=uuid,
        file_type=file_type,
        page_count=page_count,
        rm_files=rm_files,
        versions=versions,
        unknown_headers=unknown,
        total_bytes=total,
    )


# --------------------------------------------------------------------------
# Renderers
# --------------------------------------------------------------------------


class Renderer(Protocol):
    """Turns a raw bundle into one attachment file inside the vault.

    `signature` must change whenever configuration that affects the output
    changes, so sync knows to re-render a notebook whose content is unchanged.
    """

    name: str
    signature: str

    def render(self, *, raw: Path, uuid: str, base_name: str, out_dir: Path) -> list[Path]:
        """Return the attachments written into `out_dir`, in reading order.

        A list rather than one file because a notebook has pages, and pages
        read better in Obsidian as images in flow than as one document behind
        a viewer.
        """


class NullRenderer:
    """The default: import raw data only, render nothing."""

    name = "none"
    signature = "none"

    def render(self, *, raw: Path, uuid: str, base_name: str, out_dir: Path) -> list[Path]:
        return []


PLACEHOLDERS = ("{raw}", "{uuid}", "{name}", "{out}")
REQUIRED_PLACEHOLDER = "{out}"


class CommandRenderer:
    """Delegates rendering to an external command chosen by the user.

    The command receives the raw bundle and the exact path it must write, so
    rmos never has to know how the tool is invoked or what it depends on. It is
    run without a shell: arguments are passed as a list, and only the four
    documented placeholders are substituted.
    """

    name = "command"

    def __init__(self, command: list[str], *, extension: str = "pdf", timeout: int = 300) -> None:
        if not command:
            raise ValueError("[render] command must not be empty")
        if not any(REQUIRED_PLACEHOLDER in argument for argument in command):
            raise ValueError(
                f"[render] command must contain the {REQUIRED_PLACEHOLDER} placeholder "
                f"(available: {', '.join(PLACEHOLDERS)})"
            )
        self.command = list(command)
        self.extension = extension.lstrip(".")
        self.timeout = timeout

    @property
    def signature(self) -> str:
        payload = json.dumps([self.command, self.extension], sort_keys=True)
        return "command:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _argv(self, mapping: dict[str, str]) -> list[str]:
        rendered = []
        for argument in self.command:
            for placeholder, value in mapping.items():
                argument = argument.replace(placeholder, value)
            rendered.append(argument)
        return rendered

    def render(self, *, raw: Path, uuid: str, base_name: str, out_dir: Path) -> list[Path]:
        attachment = out_dir / f"{safe_name(base_name, uuid)}.{self.extension}"
        out_dir.mkdir(parents=True, exist_ok=True)
        staging = out_dir / f".{attachment.name}.rmos-partial"
        staging.unlink(missing_ok=True)

        argv = self._argv(
            {
                "{raw}": str(raw),
                "{uuid}": uuid,
                "{name}": base_name,
                "{out}": str(staging),
            }
        )
        if not shutil.which(argv[0]):
            raise RenderError(f"render command not found on PATH: {argv[0]}")

        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout, check=False)
        except OSError as exc:
            raise RenderError(f"could not run render command: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            staging.unlink(missing_ok=True)
            raise RenderError(f"render command timed out after {self.timeout}s") from exc

        if result.returncode != 0:
            staging.unlink(missing_ok=True)
            lines = (result.stderr or result.stdout or "").strip().splitlines()
            detail = lines[-1] if lines else "no output"
            raise RenderError(f"render command failed (exit {result.returncode}): {detail}")
        if not staging.is_file() or staging.stat().st_size == 0:
            staging.unlink(missing_ok=True)
            raise RenderError("render command produced no output file")

        staging.replace(attachment)
        return [attachment]


class ThumbnailRenderer:
    """Uses the page previews the tablet has already drawn.

    No parser, so nothing here can misread a stroke format: these are the
    device's own renders, copied out in reading order. They are small - 384x512
    against a 1404x1872 screen - which is fine for drawings and large writing
    and may not be for dense notes. That is the trade this backend makes, and
    it costs nothing to try before committing to a real renderer.
    """

    name = "thumbnails"
    signature = "thumbnails:1"

    def render(self, *, raw: Path, uuid: str, base_name: str, out_dir: Path) -> list[Path]:
        content_file = raw / f"{uuid}.content"
        if not content_file.is_file():
            return []
        try:
            content = json.loads(content_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RenderError(f"could not read {content_file.name}: {exc}") from exc
        if not isinstance(content, dict):
            return []

        # Books and PDFs have page previews too, but a page of someone else's
        # book is not a note, and there would be hundreds of them.
        if str(content.get("fileType") or "") != "notebook":
            return []

        source_dir = raw / f"{uuid}.thumbnails"
        if not source_dir.is_dir():
            return []

        out_dir.mkdir(parents=True, exist_ok=True)
        stem = safe_name(base_name, uuid)
        written: list[Path] = []
        for number, page_id in enumerate(page_order(content), start=1):
            thumbnail = source_dir / f"{page_id}.png"
            if not thumbnail.is_file():
                continue
            # Named after the notebook so two notebooks' pages cannot collide
            # in a vault that resolves attachments by filename.
            target = out_dir / f"{stem} p{number:02d}.png"
            shutil.copyfile(thumbnail, target)
            written.append(target)
        return written


def build_renderer(settings: dict) -> Renderer:
    """Construct the configured renderer from the `[render]` config table."""
    backend = settings.get("backend", "none")
    if backend in ("none", "", None):
        return NullRenderer()
    if backend == "thumbnails":
        return ThumbnailRenderer()
    if backend == "command":
        command = settings.get("command")
        if not isinstance(command, list) or not all(isinstance(a, str) for a in command):
            raise ValueError("[render] command must be a list of strings")
        return CommandRenderer(
            command,
            extension=str(settings.get("extension", "pdf")),
            timeout=int(settings.get("timeout", 300)),
        )
    raise ValueError(
        f"Unknown [render] backend: {backend!r} (expected 'none', 'thumbnails' or 'command')"
    )
