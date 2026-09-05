"""Configuration: loading, layering, and the one file rmos writes itself.

Two files, on purpose:

- `config.toml` is yours. It carries comments explaining every option, and
  nothing here ever rewrites it - a machine that reformats a hand-written file
  eventually eats the comments.
- `config.local.toml` is written by `rmos config set`, which is how the
  Omarchy plugin persists what you pick in its UI. It is machine-owned, so it
  can be regenerated freely.

The local file wins where the two overlap.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("~/.config/rmos/config.toml")
DEFAULT_STATE_PATH = "~/.local/state/rmos/state.json"

LOCAL_SUFFIX = ".local.toml"


class ConfigError(RuntimeError):
    """A configuration problem we can explain without a traceback."""


# --------------------------------------------------------------------------
# The config object
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
    selection_sources: tuple[str, ...] = ("file", "tag")
    selection_tags: tuple[str, ...] = ("obsidian",)

    @property
    def remote(self) -> str:
        return f"{self.user}@{self.host}"

    @property
    def vault_source(self) -> Path:
        return self.vault / self.source

    @property
    def selection_tag(self) -> str:
        """The first configured tag, for messages that name just one."""
        return self.selection_tags[0] if self.selection_tags else ""


def local_path_for(path: Path) -> Path:
    """The machine-written companion to a config file."""
    return path.with_name(path.name.removesuffix(".toml") + LOCAL_SUFFIX)


def _read_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def merge_tables(base: dict, overlay: dict) -> dict:
    """Overlay wins, one table deep. Lists replace rather than concatenate."""
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def _selection_tags(sel: dict) -> tuple[str, ...]:
    """Read `tags`, falling back to the older singular `tag`."""
    if "tags" in sel:
        raw = sel["tags"]
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
            raise ConfigError("[selection] tags must be a list of strings")
        return tuple(t for t in (s.strip() for s in raw) if t)
    if "tag" in sel:
        tag = str(sel["tag"]).strip()
        return (tag,) if tag else ()
    return ("obsidian",)


def config_from_tables(raw: dict, *, origin: Path | None = None) -> Config:
    where = f"{origin}: " if origin else ""
    rm = raw.get("remarkable", {})
    ob = raw.get("obsidian", {})
    st = raw.get("rmos", {})
    sel = raw.get("selection", {})

    vault = ob.get("vault")
    if not vault:
        raise ConfigError(f"{where}[obsidian] vault is required.")

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
        selection_sources=tuple(sel.get("sources", ["file", "tag"])),
        selection_tags=_selection_tags(sel),
    )


def load_config(path: Path | None) -> Config:
    path = (path or DEFAULT_CONFIG_PATH).expanduser()
    local = local_path_for(path)
    if not path.exists() and not local.exists():
        raise ConfigError(f"No config at {path}. Run `rmos init-config` to create one.")
    return config_from_tables(merge_tables(_read_toml(path), _read_toml(local)), origin=path)


# --------------------------------------------------------------------------
# Reading and writing individual settings
# --------------------------------------------------------------------------

# Only these may be written by `rmos config set`. Anything that could point a
# subprocess somewhere - the render command, ssh options - stays hand-edited,
# so a UI bug can never turn into command execution.
WRITABLE_KEYS: dict[str, type | tuple[type, ...]] = {
    "remarkable.host": str,
    "remarkable.user": str,
    "remarkable.multiplex": bool,
    "remarkable.connect_timeout": int,
    "obsidian.vault": str,
    "obsidian.source": str,
    "selection.sources": list,
    "selection.tags": list,
    "selection.tag": str,
}


def split_key(key: str) -> tuple[str, str]:
    table, _, name = key.partition(".")
    if not table or not name or "." in name:
        raise ConfigError(f"Setting names look like 'table.name', not {key!r}.")
    return table, name


def read_setting(path: Path, key: str) -> dict:
    """Report a setting's effective value and where it came from."""
    table, name = split_key(key)
    path = path.expanduser()
    local = local_path_for(path)
    base_tables, local_tables = _read_toml(path), _read_toml(local)

    if name in local_tables.get(table, {}):
        return {"key": key, "value": local_tables[table][name], "source": str(local)}
    if name in base_tables.get(table, {}):
        return {"key": key, "value": base_tables[table][name], "source": str(path)}
    return {"key": key, "value": None, "source": None}


def write_setting(path: Path, key: str, value: Any) -> Path:
    """Set one value in the machine-written local config, and return its path."""
    table, name = split_key(key)
    if key not in WRITABLE_KEYS:
        raise ConfigError(
            f"{key} is not settable by `rmos config set`. "
            f"Settable: {', '.join(sorted(WRITABLE_KEYS))}."
        )
    expected = WRITABLE_KEYS[key]
    if expected is bool and not isinstance(value, bool):
        raise ConfigError(f"{key} expects true or false.")
    if expected is int and (isinstance(value, bool) or not isinstance(value, int)):
        raise ConfigError(f"{key} expects a whole number.")
    if expected is str and not isinstance(value, str):
        raise ConfigError(f"{key} expects a string.")
    if expected is list and (not isinstance(value, list) or not all(isinstance(i, str) for i in value)):
        raise ConfigError(f"{key} expects a list of strings.")

    local = local_path_for(path.expanduser())
    tables = _read_toml(local)
    tables.setdefault(table, {})[name] = value
    local.parent.mkdir(parents=True, exist_ok=True)
    tmp = local.with_name(local.name + ".rmos-tmp")
    tmp.write_text(dump_toml(tables), encoding="utf-8")
    os.replace(tmp, local)
    return local


def unset_setting(path: Path, key: str) -> Path:
    table, name = split_key(key)
    local = local_path_for(path.expanduser())
    tables = _read_toml(local)
    if name in tables.get(table, {}):
        del tables[table][name]
        if not tables[table]:
            del tables[table]
        tmp = local.with_name(local.name + ".rmos-tmp")
        tmp.write_text(dump_toml(tables), encoding="utf-8")
        os.replace(tmp, local)
    return local


# --------------------------------------------------------------------------
# A TOML writer for the one file we own
# --------------------------------------------------------------------------

HEADER = (
    "# Written by `rmos config set` - edit config.toml instead.\n"
    "# Values here override the ones there.\n"
)


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        escaped = escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        return f'"{escaped}"'
    raise ConfigError(f"Cannot write {type(value).__name__} to config.")


def dump_toml(tables: dict) -> str:
    """Serialise the local config.

    Deliberately narrow: this writes only the shapes rmos itself stores -
    tables of strings, booleans, whole numbers and string lists. Anything else
    is an error rather than a guess, because the alternative is producing a
    file that silently fails to parse on the next read.
    """
    out = [HEADER]
    for table in sorted(tables):
        values = tables[table]
        if not isinstance(values, dict):
            raise ConfigError(f"Expected a table at [{table}].")
        if not values:
            continue
        out.append(f"\n[{table}]\n")
        for name in sorted(values):
            value = values[name]
            if isinstance(value, list):
                items = ", ".join(_scalar(item) for item in value)
                out.append(f"{name} = [{items}]\n")
            else:
                out.append(f"{name} = {_scalar(value)}\n")
    return "".join(out)
