"""Tests for how a notebook gets marked for export.

Tagging is the on-device action: it marks a notebook without moving it out of
the folder the user filed it in. `selected.txt` remains as a second source.
"""

import argparse

import pytest

from rmos import cli
from rmos.core import (
    build_index,
    document_tags,
    folder_path,
    page_tags,
    select_by_tag,
    tag_census,
    unreadable_tag_documents,
)

UUID = "550e8400-e29b-41d4-a716-446655440000"
OTHER = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
THIRD = "3ccd1c14-c21e-411c-b129-e3f001b558a4"


def index_of(*docs):
    """Build an index from (uuid, name, tags, **metadata overrides) tuples."""
    metadata, content = {}, {}
    for uuid, name, tags, *rest in docs:
        extra = rest[0] if rest else {}
        metadata[uuid] = {"visibleName": name, "type": "DocumentType", "parent": "", **extra}
        content[uuid] = {"tags": tags}
    return build_index(metadata, content)


def page_tagged(uuid, name, *names):
    """An index holding one document tagged only on its pages."""
    return build_index(
        {uuid: {"visibleName": name, "type": "DocumentType", "parent": ""}},
        {uuid: {"tags": [], "pageTags": [{"name": n, "pageId": f"p{i}", "timestamp": 1} for i, n in enumerate(names)]}},
    )


# --------------------------------------------------------------------------
# Tag decoding
# --------------------------------------------------------------------------


def test_tags_encoded_as_objects_are_read():
    assert document_tags({"tags": [{"name": "obsidian", "timestamp": 1}]}) == ["obsidian"]


def test_tags_encoded_as_plain_strings_are_read():
    """Which encoding a firmware writes should not be something we guess at."""
    assert document_tags({"tags": ["obsidian", "todo"]}) == ["obsidian", "todo"]


def test_mixed_and_malformed_tag_entries_are_tolerated():
    content = {"tags": ["a", {"name": "b"}, {"nope": "c"}, 42, None, {"name": ""}, "  d  "]}
    assert document_tags(content) == ["a", "b", "d"]


@pytest.mark.parametrize("content", [{}, {"tags": None}, {"tags": "obsidian"}, {"tags": []}, {"tags": {}}])
def test_absent_or_unusable_tags_yield_nothing(content):
    assert document_tags(content) == []


# --------------------------------------------------------------------------
# Selecting by tag
# --------------------------------------------------------------------------


def test_a_tagged_document_is_selected():
    index = index_of((UUID, "Alpha", [{"name": "obsidian"}]), (OTHER, "Beta", []))
    assert select_by_tag(index, "obsidian") == [UUID]


def test_tag_matching_ignores_case_on_both_sides():
    index = index_of((UUID, "Alpha", ["Obsidian"]))
    assert select_by_tag(index, "obsidian") == [UUID]
    assert select_by_tag(index, "OBSIDIAN") == [UUID]


def test_surrounding_whitespace_does_not_prevent_a_match():
    index = index_of((UUID, "Alpha", ["  obsidian  "]))
    assert select_by_tag(index, " obsidian ") == [UUID]


def test_a_different_tag_does_not_select():
    index = index_of((UUID, "Alpha", ["todo", "reading"]))
    assert select_by_tag(index, "obsidian") == []


def test_an_empty_tag_never_selects_everything():
    index = index_of((UUID, "Alpha", ["obsidian"]), (OTHER, "Beta", []))
    assert select_by_tag(index, "") == []
    assert select_by_tag(index, "   ") == []


def test_deleted_and_trashed_documents_are_skipped():
    index = index_of(
        (UUID, "Alpha", ["obsidian"], {"deleted": True}),
        (OTHER, "Beta", ["obsidian"], {"parent": "trash"}),
        (THIRD, "Gamma", ["obsidian"]),
    )
    assert select_by_tag(index, "obsidian") == [THIRD]


def test_a_tagged_folder_is_not_a_document():
    index = index_of((UUID, "Notes", ["obsidian"], {"type": "CollectionType"}))
    assert select_by_tag(index, "obsidian") == []


def test_selection_order_is_deterministic():
    index = index_of((OTHER, "B", ["obsidian"]), (UUID, "A", ["obsidian"]))
    assert select_by_tag(index, "obsidian") == sorted([UUID, OTHER])


def test_a_document_without_content_is_handled():
    index = build_index({UUID: {"visibleName": "Alpha", "type": "DocumentType"}}, {})
    assert select_by_tag(index, "obsidian") == []
    assert index[UUID].visible_name == "Alpha"


def test_a_nameless_document_falls_back_to_its_uuid():
    assert build_index({UUID: {"type": "DocumentType"}}, {})[UUID].visible_name == UUID


# --------------------------------------------------------------------------
# Tag census
# --------------------------------------------------------------------------


def test_census_counts_documents_per_tag():
    index = index_of((UUID, "A", ["obsidian", "todo"]), (OTHER, "B", ["obsidian"]), (THIRD, "C", []))
    assert tag_census(index) == [("obsidian", 2), ("todo", 1)]


def test_census_does_not_double_count_case_variants_on_one_document():
    assert tag_census(index_of((UUID, "A", ["Obsidian", "obsidian"]))) == [("Obsidian", 1)]


def test_census_excludes_deleted_documents():
    index = index_of((UUID, "A", ["obsidian"], {"deleted": True}), (OTHER, "B", ["obsidian"]))
    assert tag_census(index) == [("obsidian", 1)]


# --------------------------------------------------------------------------
# Combining sources
# --------------------------------------------------------------------------


@pytest.fixture
def sources(monkeypatch):
    calls = {"file": 0, "index": 0}
    state = {"file": [], "tagged": []}

    def read_file(_ssh):
        calls["file"] += 1
        return list(state["file"])

    def read_index(_ssh):
        calls["index"] += 1
        return index_of(*[(u, u, ["obsidian"]) for u in state["tagged"]])

    monkeypatch.setattr(cli, "read_selection_file", read_file)
    monkeypatch.setattr(cli, "read_index", read_index)
    return state, calls


def config(**kw):
    return cli.Config(vault=cli.Path("/vault"), **kw)


def test_both_sources_are_unioned(sources):
    state, _ = sources
    state["file"] = [UUID]
    state["tagged"] = [OTHER]

    assert sorted(cli.read_selection(None, config())) == sorted([UUID, OTHER])


def test_a_notebook_selected_twice_appears_once(sources):
    state, _ = sources
    state["file"] = [UUID]
    state["tagged"] = [UUID]

    assert cli.read_selection(None, config()) == [UUID]


def test_the_tag_source_can_be_turned_off(sources):
    state, calls = sources
    state["file"] = [UUID]
    state["tagged"] = [OTHER]

    result = cli.read_selection(None, config(selection_sources=("file",)))

    assert result == [UUID]
    assert calls["index"] == 0, "the index is a 3.7MB read; do not pay for it unless it is used"


def test_the_file_source_can_be_turned_off(sources):
    state, calls = sources
    state["file"] = [UUID]
    state["tagged"] = [OTHER]

    result = cli.read_selection(None, config(selection_sources=("tag",)))

    assert result == [OTHER]
    assert calls["file"] == 0


def test_the_configured_tag_is_honoured(sources, monkeypatch):
    monkeypatch.setattr(cli, "read_index", lambda _ssh: index_of((UUID, "A", ["notes"]), (OTHER, "B", ["obsidian"])))

    assert cli.read_selection(None, config(selection_sources=("tag",), selection_tags=("notes",))) == [UUID]


def test_an_unknown_source_is_rejected_rather_than_ignored(sources):
    """Silently ignoring a typo would mean silently syncing nothing."""
    with pytest.raises(cli.RmosError, match="Unknown"):
        cli.read_selection(None, config(selection_sources=("file", "tagz")))


def test_an_empty_source_list_is_rejected(sources):
    with pytest.raises(cli.RmosError, match="empty"):
        cli.read_selection(None, config(selection_sources=()))


# --------------------------------------------------------------------------
# Config plumbing
# --------------------------------------------------------------------------


def test_selection_defaults_to_both_sources(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[obsidian]\nvault = "/vault"\n', encoding="utf-8")

    cfg = cli.load_config(path)

    assert cfg.selection_sources == ("file", "tag")
    assert cfg.selection_tag == "obsidian"


def test_selection_can_be_configured(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[obsidian]\nvault = "/vault"\n\n[selection]\nsources = ["tag"]\ntag = "export"\n',
        encoding="utf-8",
    )

    cfg = cli.load_config(path)

    assert cfg.selection_sources == ("tag",)
    assert cfg.selection_tag == "export"


def test_tags_command_reports_when_nothing_is_tagged(monkeypatch, capsys):
    monkeypatch.setattr(cli, "make_ssh", lambda *a, **k: None)
    monkeypatch.setattr(cli, "read_index", lambda _ssh: index_of((UUID, "A", [])))

    code = cli.cmd_tags(config(), argparse.Namespace(verbose=False, batch=False, wait=0, all=False))

    assert code == 0
    assert "obsidian" in capsys.readouterr().out


def test_tags_command_marks_the_configured_tag(monkeypatch, capsys):
    monkeypatch.setattr(cli, "make_ssh", lambda *a, **k: None)
    monkeypatch.setattr(cli, "read_index", lambda _ssh: index_of((UUID, "A", ["obsidian"]), (OTHER, "B", ["todo"])))

    cli.cmd_tags(config(), argparse.Namespace(verbose=False, batch=False, wait=0, all=False))

    out = capsys.readouterr().out
    assert "selected for export" in out
    assert "todo" in out


# --------------------------------------------------------------------------
# Safety net for an unexpected tag encoding
# --------------------------------------------------------------------------


def test_an_undecodable_tag_list_is_flagged_not_swallowed():
    """A firmware could encode tags a third way. Fail loudly, not silently."""
    index = build_index(
        {UUID: {"visibleName": "A", "type": "DocumentType"}},
        {UUID: {"tags": [{"label": "obsidian"}]}},
    )
    assert index[UUID].tags == ()
    assert index[UUID].tags_unreadable is True
    assert [e.uuid for e in unreadable_tag_documents(index)] == [UUID]


def test_an_empty_tag_list_is_not_flagged():
    index = index_of((UUID, "A", []))
    assert index[UUID].tags_unreadable is False
    assert unreadable_tag_documents(index) == []


def test_a_decodable_tag_list_is_not_flagged():
    index = index_of((UUID, "A", [{"name": "obsidian"}]))
    assert index[UUID].tags_unreadable is False


def test_sync_warns_when_tags_cannot_be_decoded(monkeypatch, capsys):
    monkeypatch.setattr(cli, "read_selection_file", lambda _ssh: [])
    monkeypatch.setattr(
        cli,
        "read_index",
        lambda _ssh: build_index(
            {UUID: {"visibleName": "Notebook A", "type": "DocumentType"}},
            {UUID: {"tags": [{"label": "obsidian"}]}},
        ),
    )

    assert cli.read_selection(None, config()) == []

    err = capsys.readouterr().err
    assert "encoding rmos does not understand" in err
    assert "Notebook A" in err


# --------------------------------------------------------------------------
# Page tags
# --------------------------------------------------------------------------
#
# Tagging a page from inside a notebook and tagging the notebook itself are
# different actions in the reMarkable UI, writing pageTags and tags. Firmware
# 20260612085811 writes a page tag as
# {"name": "sync", "pageId": ..., "timestamp": ...}.


def test_a_page_tag_selects_the_whole_notebook():
    assert select_by_tag(page_tagged(UUID, "Quick sheets", "sync"), "sync") == [UUID]


def test_the_firmware_page_tag_shape_is_decoded():
    content = {"pageTags": [{"name": "sync", "pageId": "ff00a3a3", "timestamp": 1788637081505}]}
    assert page_tags(content) == ["sync"]


def test_a_tag_on_several_pages_still_selects_the_notebook_once():
    index = page_tagged(UUID, "A", "sync", "sync", "sync")
    assert select_by_tag(index, "sync") == [UUID]
    assert index[UUID].page_tags == ("sync",)


def test_document_and_page_tags_are_merged_without_duplicates():
    index = build_index(
        {UUID: {"visibleName": "A", "type": "DocumentType"}},
        {UUID: {"tags": [{"name": "obsidian"}], "pageTags": [{"name": "Obsidian"}, {"name": "todo"}]}},
    )
    assert index[UUID].all_tags == ("obsidian", "todo")


def test_either_kind_of_tag_selects_on_its_own():
    doc_only = index_of((UUID, "A", [{"name": "sync"}]))
    assert select_by_tag(doc_only, "sync") == [UUID]
    assert select_by_tag(page_tagged(OTHER, "B", "sync"), "sync") == [OTHER]


def test_census_counts_a_page_tagged_notebook():
    assert tag_census(page_tagged(UUID, "A", "sync", "sync")) == [("sync", 1)]


def test_an_undecodable_page_tag_is_flagged():
    index = build_index(
        {UUID: {"visibleName": "A", "type": "DocumentType"}},
        {UUID: {"tags": [], "pageTags": [{"label": "sync"}]}},
    )
    assert index[UUID].tags_unreadable is True


def test_a_readable_page_tag_is_not_flagged():
    assert page_tagged(UUID, "A", "sync")[UUID].tags_unreadable is False


# --------------------------------------------------------------------------
# Folder paths
# --------------------------------------------------------------------------


def folders(*entries):
    metadata = {}
    for uuid, name, kind, parent in entries:
        metadata[uuid] = {"visibleName": name, "type": kind, "parent": parent}
    return build_index(metadata, {})


def test_a_top_level_notebook_has_no_folder():
    index = folders((UUID, "A", "DocumentType", ""))
    assert folder_path(index, UUID) == ""


def test_nested_folders_read_outermost_first():
    index = folders(
        ("f1", "01.projects", "CollectionType", ""),
        ("f2", "alpha", "CollectionType", "f1"),
        (UUID, "A", "DocumentType", "f2"),
    )
    assert folder_path(index, UUID) == "01.projects/alpha"


def test_a_trashed_notebook_reports_no_folder():
    index = folders((UUID, "A", "DocumentType", "trash"))
    assert folder_path(index, UUID) == ""


def test_a_parent_that_does_not_exist_stops_the_walk():
    index = folders((UUID, "A", "DocumentType", "missing-folder"))
    assert folder_path(index, UUID) == ""


def test_a_parent_cycle_terminates_instead_of_hanging():
    """The tablet should never produce this, but a hang would be unrecoverable."""
    index = folders(
        ("f1", "one", "CollectionType", "f2"),
        ("f2", "two", "CollectionType", "f1"),
        (UUID, "A", "DocumentType", "f1"),
    )
    assert folder_path(index, UUID) in ("two/one", "one/two")
