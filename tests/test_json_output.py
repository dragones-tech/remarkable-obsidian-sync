"""Tests for the machine-readable output the Omarchy plugin consumes.

The plugin parses stdout with JSON.parse, so every `--json` run must emit
exactly one object and nothing else. A stray progress line would break it.
"""

import argparse
import dataclasses
import json

import pytest

from rmos import cli
from rmos.core import build_index

UUID = "550e8400-e29b-41d4-a716-446655440000"
OTHER = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
FOLDER = "11111111-2222-3333-4444-555555555555"


def only_object(captured):
    """Assert stdout is one JSON object, and return it."""
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected exactly one line of JSON, got {lines!r}"
    return json.loads(lines[0])


def args(**kw):
    base = {"verbose": False, "batch": False, "wait": 0, "json": True}
    return argparse.Namespace(**{**base, **kw})


@pytest.fixture
def cfg(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Sources" / "reMarkable").mkdir(parents=True)
    return cli.Config(vault=vault, source="Sources/reMarkable", state=tmp_path / "state.json")


@pytest.fixture
def tablet(monkeypatch):
    """A tablet holding one tagged notebook inside a folder."""
    index = build_index(
        {
            FOLDER: {"visibleName": "01.projects", "type": "CollectionType", "parent": ""},
            UUID: {"visibleName": "Quick sheets", "type": "DocumentType", "parent": FOLDER},
            OTHER: {"visibleName": "Trashed", "type": "DocumentType", "parent": "trash"},
        },
        {UUID: {"pageTags": [{"name": "sync", "pageId": "p1", "timestamp": 1}]}},
    )
    monkeypatch.setattr(cli, "make_ssh", lambda *a, **k: object())
    monkeypatch.setattr(cli, "read_index", lambda _ssh: index)
    monkeypatch.setattr(cli, "read_selection_file", lambda _ssh: [])
    return index


# --------------------------------------------------------------------------
# The shape of each command's JSON
# --------------------------------------------------------------------------


def test_tags_emits_one_object(cfg, tablet, capsys):
    cli.cmd_tags(cfg, args(all=False))
    payload = only_object(capsys.readouterr())
    assert payload["tags"] == [{"name": "sync", "count": 1, "selected": False}]
    assert payload["documents"][0]["name"] == "Quick sheets"


def test_tags_marks_the_configured_tag(cfg, tablet, capsys):
    cli.cmd_tags(dataclasses.replace(cfg, selection_tags=("sync",)), args(all=False))
    assert only_object(capsys.readouterr())["tags"][0]["selected"] is True


def test_index_carries_what_a_picker_row_needs(cfg, tablet, capsys):
    cli.cmd_index(dataclasses.replace(cfg, selection_tags=("sync",)), args())
    payload = only_object(capsys.readouterr())

    assert payload["selection"] == {"sources": ["file", "tag"], "tags": ["sync"]}
    assert len(payload["documents"]) == 1, "trashed notebooks are not offered"
    row = payload["documents"][0]
    assert row == {
        "uuid": UUID,
        "name": "Quick sheets",
        "folder": "01.projects",
        "tags": ["sync"],
        "selected": True,
        "selected_by": ["tag"],
        "synced": False,
        "destination": None,
        "note": None,
    }


def test_index_reports_a_notebook_selected_by_file(cfg, tablet, monkeypatch, capsys):
    monkeypatch.setattr(cli, "read_selection_file", lambda _ssh: [UUID])

    cli.cmd_index(cfg, args())

    row = only_object(capsys.readouterr())["documents"][0]
    assert row["selected"] is True
    assert row["selected_by"] == ["file"]


def test_index_reports_both_sources_when_both_apply(cfg, tablet, monkeypatch, capsys):
    monkeypatch.setattr(cli, "read_selection_file", lambda _ssh: [UUID])

    cli.cmd_index(dataclasses.replace(cfg, selection_tags=("sync",)), args())

    assert only_object(capsys.readouterr())["documents"][0]["selected_by"] == ["file", "tag"]


def test_doctor_emits_one_object_with_a_verdict(cfg, monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda _tool: None)

    code = cli.cmd_doctor(cfg, args())

    payload = only_object(capsys.readouterr())
    assert payload["ok"] is False
    assert code == 1
    assert any(c["name"] == "local `ssh`" for c in payload["checks"])
    assert all({"name", "ok", "detail", "required"} <= set(c) for c in payload["checks"])


def test_config_get_emits_one_object(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text('[obsidian]\nvault = "/vault"\n', encoding="utf-8")

    cli.cmd_config(None, args(config=path, action="get", key="obsidian.vault"))

    assert only_object(capsys.readouterr()) == {
        "key": "obsidian.vault",
        "value": "/vault",
        "source": str(path),
    }


def test_config_set_parses_a_json_value(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text('[obsidian]\nvault = "/vault"\n', encoding="utf-8")

    cli.cmd_config(None, args(config=path, action="set", key="selection.tags", value='["a","b"]'))

    assert only_object(capsys.readouterr())["value"] == ["a", "b"]
    assert cli.load_config(path).selection_tags == ("a", "b")


def test_config_set_accepts_a_bare_word_as_a_string(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text('[obsidian]\nvault = "/vault"\n', encoding="utf-8")

    cli.cmd_config(None, args(config=path, action="set", key="remarkable.host", value="10.11.99.2"))

    assert cli.load_config(path).host == "10.11.99.2"


# --------------------------------------------------------------------------
# Argument placement
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "doctor"],
        ["doctor", "--json"],
        ["--json", "sync", "--dry-run"],
        ["sync", "--dry-run", "--json"],
        ["tags", "--json", "--all"],
    ],
)
def test_json_is_accepted_before_or_after_the_subcommand(argv):
    """`rmos sync --json` is what anyone would type; it must not be an error."""
    parsed = cli.build_parser().parse_args(argv)
    assert parsed.json is True


def test_a_flag_given_before_the_subcommand_is_not_clobbered():
    parsed = cli.build_parser().parse_args(["--json", "--batch", "sync"])
    assert parsed.json is True
    assert parsed.batch is True


def test_flags_default_to_off():
    parsed = cli.build_parser().parse_args(["sync"])
    assert parsed.json is False
    assert parsed.batch is False
    assert parsed.wait == 0


# --------------------------------------------------------------------------
# Where a synced note lives
# --------------------------------------------------------------------------


def synced_state(tmp_path, cfg, uuid=UUID, name="Quick sheets", write_note=True):
    """A vault holding one imported notebook, and the state that tracks it."""
    dest = cfg.vault_source / name
    dest.mkdir(parents=True, exist_ok=True)
    if write_note:
        (dest / f"{name}.md").write_text(f"---\nremarkable_id: {uuid}\n---\n", encoding="utf-8")
    cli.save_state(cfg.state, {"documents": {uuid: {
        "fingerprint": "abc", "visible_name": name, "destination": str(dest),
    }}})
    return dest


def test_status_reports_where_a_synced_note_lives(cfg, tablet, tmp_path, monkeypatch, capsys):
    """The widget opens the note on click, so the path must arrive with the
    report rather than costing another round trip at click time."""
    dest = synced_state(tmp_path, cfg)
    monkeypatch.setattr(cli, "read_selection", lambda _ssh, _cfg: [UUID])
    monkeypatch.setattr(cli, "remote_metadata", lambda _ssh, _u: {"visibleName": "Quick sheets"})
    monkeypatch.setattr(cli, "remote_fingerprint", lambda _ssh, _u: ("abc", []))

    cli.cmd_status(cfg, args())

    notebook = only_object(capsys.readouterr())["notebooks"][0]
    assert notebook["destination"] == str(dest)
    assert notebook["note"] == str(dest / "Quick sheets.md")


def test_a_notebook_that_was_never_synced_has_no_note(cfg, tablet, monkeypatch, capsys):
    monkeypatch.setattr(cli, "read_selection", lambda _ssh, _cfg: [UUID])
    monkeypatch.setattr(cli, "remote_metadata", lambda _ssh, _u: {"visibleName": "Quick sheets"})
    monkeypatch.setattr(cli, "remote_fingerprint", lambda _ssh, _u: ("abc", []))

    cli.cmd_status(cfg, args())

    notebook = only_object(capsys.readouterr())["notebooks"][0]
    assert notebook["note"] is None
    assert notebook["destination"] is None


def test_a_note_deleted_from_the_vault_is_reported_as_absent(cfg, tablet, tmp_path, monkeypatch, capsys):
    """State can outlive the file: someone may have deleted the note."""
    synced_state(tmp_path, cfg, write_note=False)
    monkeypatch.setattr(cli, "read_selection", lambda _ssh, _cfg: [UUID])
    monkeypatch.setattr(cli, "remote_metadata", lambda _ssh, _u: {"visibleName": "Quick sheets"})
    monkeypatch.setattr(cli, "remote_fingerprint", lambda _ssh, _u: ("abc", []))

    cli.cmd_status(cfg, args())

    notebook = only_object(capsys.readouterr())["notebooks"][0]
    assert notebook["note"] is None, "nothing to open, so do not offer to open it"
    assert notebook["destination"] is not None


def test_notebooks_no_longer_selected_still_say_where_they_are(cfg, tablet, tmp_path, monkeypatch, capsys):
    dest = synced_state(tmp_path, cfg)
    monkeypatch.setattr(cli, "read_selection", lambda _ssh, _cfg: [])

    cli.cmd_status(cfg, args())

    kept = only_object(capsys.readouterr())["no_longer_selected"][0]
    assert kept["note"] == str(dest / "Quick sheets.md")


def test_the_index_says_where_a_synced_note_is_so_the_picker_can_open_it(cfg, tablet, tmp_path, capsys):
    dest = synced_state(tmp_path, cfg)

    cli.cmd_index(cfg, args())

    row = only_object(capsys.readouterr())["documents"][0]
    assert row["synced"] is True
    assert row["note"] == str(dest / "Quick sheets.md")


def test_an_unsynced_notebook_offers_nothing_to_open(cfg, tablet, capsys):
    cli.cmd_index(cfg, args())
    row = only_object(capsys.readouterr())["documents"][0]
    assert row["synced"] is False
    assert row["note"] is None
