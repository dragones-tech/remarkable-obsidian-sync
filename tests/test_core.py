import hashlib
import json
from pathlib import Path

import pytest

from rmos.core import (
    Notebook,
    destination_conflicts,
    fingerprint_entries,
    fingerprint_tree,
    frontmatter_id,
    notebook_from_metadata,
    parse_hash_listing,
    parse_metadata,
    parse_selected,
    plan_destination,
    prune_stale_notes,
    render_markdown,
    replace_tree,
    safe_name,
    validate_uuid,
    write_text_atomic,
)

UUID = "550e8400-e29b-41d4-a716-446655440000"
OTHER = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


# --------------------------------------------------------------------------
# UUID validation
# --------------------------------------------------------------------------


def test_uuid_is_normalised_to_lowercase():
    assert validate_uuid(UUID.upper()) == UUID
    assert validate_uuid(f"  {UUID}  ") == UUID


@pytest.mark.parametrize(
    "value",
    [
        "../../bad",
        "",
        "550e8400e29b41d4a716446655440000",
        "550e8400-e29b-41d4-a716-44665544000",
        "zzzzzzzz-e29b-41d4-a716-446655440000",
        f"{UUID} extra",
        "550e8400-e29b-41d4-a716-446655440000/..",
    ],
)
def test_uuid_rejects_anything_that_could_escape_a_path(value):
    with pytest.raises(ValueError):
        validate_uuid(value)


# --------------------------------------------------------------------------
# selected.txt contract
# --------------------------------------------------------------------------


def test_selected_skips_comments_and_deduplicates():
    text = f"# comment\n{UUID}\n\n{UUID}\n{OTHER}\n"
    selection = parse_selected(text)
    assert selection.uuids == [UUID, OTHER]
    assert selection.invalid == []


def test_selected_reports_bad_lines_instead_of_aborting():
    selection = parse_selected(f"{UUID}\nnot-a-uuid\n{OTHER}\n")
    assert selection.uuids == [UUID, OTHER]
    assert selection.invalid == ["not-a-uuid"]


def test_selected_preserves_device_ordering():
    assert parse_selected(f"{OTHER}\n{UUID}\n").uuids == [OTHER, UUID]


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------


def test_metadata_must_be_an_object():
    assert parse_metadata('{"visibleName": "A"}') == {"visibleName": "A"}
    with pytest.raises(ValueError):
        parse_metadata("[1, 2]")
    with pytest.raises(json.JSONDecodeError):
        parse_metadata("not json")


def test_notebook_from_metadata_reads_name_and_modified_time():
    nb = notebook_from_metadata(UUID.upper(), {"visibleName": "  Project Alpha  ", "lastModified": 1700000000000})
    assert nb == Notebook(uuid=UUID, visible_name="Project Alpha", last_modified="1700000000000")


@pytest.mark.parametrize("metadata", [{}, {"visibleName": ""}, {"visibleName": "   "}, {"visibleName": 42}])
def test_notebook_falls_back_to_uuid_when_name_is_unusable(metadata):
    assert notebook_from_metadata(UUID, metadata).visible_name == UUID


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Project: A/B", "Project_ A_B"),
        ("..", UUID),
        (".", UUID),
        ("   ", UUID),
        ("", UUID),
        ("a<b>c|d?e*f", "a_b_c_d_e_f"),
        ("Trailing dot.", "Trailing dot"),
    ],
)
def test_safe_name_never_yields_a_traversal_or_empty_name(raw, expected):
    assert safe_name(raw, UUID) == expected


def test_destination_is_stable_for_the_same_name(tmp_path):
    first = plan_destination(tmp_path, {}, UUID, "Notes")
    second = plan_destination(tmp_path, {UUID: {"destination": str(first)}}, UUID, "Notes")
    assert first == second == tmp_path / "Notes"


def test_two_notebooks_sharing_a_name_get_distinct_folders(tmp_path):
    first = plan_destination(tmp_path, {}, UUID, "Notes")
    documents = {UUID: {"destination": str(first)}}
    second = plan_destination(tmp_path, documents, OTHER, "Notes")
    assert second != first
    assert second == tmp_path / f"Notes ({OTHER[:8]})"


def test_destination_avoids_a_folder_owned_by_another_notebook(tmp_path):
    occupied = tmp_path / "Notes"
    occupied.mkdir()
    (occupied / "Notes.md").write_text(f"---\nremarkable_id: {OTHER}\n---\n", encoding="utf-8")
    assert plan_destination(tmp_path, {}, UUID, "Notes") == tmp_path / f"Notes ({UUID[:8]})"


def test_destination_reuses_a_folder_this_notebook_already_owns(tmp_path):
    mine = tmp_path / "Notes"
    mine.mkdir()
    (mine / "Notes.md").write_text(f"---\nremarkable_id: {UUID}\n---\n", encoding="utf-8")
    assert plan_destination(tmp_path, {}, UUID, "Notes") == mine


def test_destination_avoids_overwriting_an_unrelated_note(tmp_path):
    theirs = tmp_path / "Notes"
    theirs.mkdir()
    (theirs / "Notes.md").write_text("hand written by the user\n", encoding="utf-8")
    assert plan_destination(tmp_path, {}, UUID, "Notes") == tmp_path / f"Notes ({UUID[:8]})"


def test_empty_existing_folder_is_not_a_conflict(tmp_path):
    (tmp_path / "Notes").mkdir()
    assert destination_conflicts(tmp_path / "Notes", UUID) is False


def test_a_file_where_the_folder_should_go_is_a_conflict(tmp_path):
    (tmp_path / "Notes").write_text("x", encoding="utf-8")
    assert destination_conflicts(tmp_path / "Notes", UUID) is True


# --------------------------------------------------------------------------
# Fingerprinting
# --------------------------------------------------------------------------


def test_fingerprint_is_deterministic_and_content_sensitive(tmp_path):
    (tmp_path / "a").write_text("one")
    first = fingerprint_tree(tmp_path)
    assert fingerprint_tree(tmp_path) == first
    (tmp_path / "a").write_text("two")
    assert fingerprint_tree(tmp_path) != first


def test_fingerprint_is_sensitive_to_paths_not_just_bytes(tmp_path):
    (tmp_path / "a").write_text("same")
    before = fingerprint_tree(tmp_path)
    (tmp_path / "a").rename(tmp_path / "b")
    assert fingerprint_tree(tmp_path) != before


def test_fingerprint_is_sensitive_to_added_files(tmp_path):
    (tmp_path / "a").write_text("one")
    before = fingerprint_tree(tmp_path)
    (tmp_path / "b").write_text("")
    assert fingerprint_tree(tmp_path) != before


def test_fingerprint_ignores_entry_order():
    entries = [("b", "22"), ("a", "11")]
    assert fingerprint_entries(entries, algo="sha256") == fingerprint_entries(reversed(entries), algo="sha256")


def test_fingerprints_from_different_algorithms_never_collide():
    entries = [("a", "11")]
    assert fingerprint_entries(entries, algo="sha256") != fingerprint_entries(entries, algo="md5")


def test_remote_checksum_listing_matches_a_local_fingerprint(tmp_path):
    """The device-side and desktop-side fingerprints must be interchangeable."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "1.rm").write_bytes(b"strokes")
    (tmp_path / "sub.metadata").write_bytes(b'{"visibleName": "x"}')

    listing = "".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(tmp_path).as_posix()}\n"
        for p in sorted(tmp_path.rglob("*"))
        if p.is_file()
    )
    remote = fingerprint_entries(parse_hash_listing(listing), algo="sha256")
    assert remote == fingerprint_tree(tmp_path, algo="sha256")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("d41d8cd98f00b204e9800998ecf8427e  file", ("file", "d41d8cd98f00b204e9800998ecf8427e")),
        ("D41D8CD98F00B204E9800998ECF8427E *file", ("file", "d41d8cd98f00b204e9800998ecf8427e")),
        ("d41d8cd98f00b204e9800998ecf8427e  dir/name with spaces.rm", ("dir/name with spaces.rm", "d41d8cd98f00b204e9800998ecf8427e")),
    ],
)
def test_checksum_lines_parse_from_both_coreutils_and_busybox(line, expected):
    assert parse_hash_listing(line + "\n") == [expected]


def test_checksum_parser_rejects_garbage():
    with pytest.raises(ValueError):
        parse_hash_listing("this is not a checksum line\n")


def test_checksum_parser_skips_blank_lines():
    assert parse_hash_listing("\n\n") == []


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def test_markdown_is_byte_identical_across_renders():
    nb = Notebook(UUID, "Project Alpha", "1700000000000")
    assert render_markdown(nb, "abc") == render_markdown(nb, "abc")


def test_markdown_carries_identity_and_fingerprint():
    nb = Notebook(UUID, "Project Alpha", "1700000000000")
    text = render_markdown(nb, "abc")
    assert f"remarkable_id: {UUID}" in text
    assert "rmos_fingerprint: abc" in text
    assert "remarkable_modified: 1700000000000" in text
    assert "# Project Alpha" in text
    assert "rendering is not enabled yet" in text


def test_markdown_embeds_the_attachment_once_rendering_exists():
    nb = Notebook(UUID, "Project Alpha", None)
    text = render_markdown(nb, "abc", pdf_name="Project Alpha.pdf")
    assert "![[attachments/Project Alpha.pdf]]" in text
    assert "rendering is not enabled yet" not in text


def test_frontmatter_id_round_trips():
    nb = Notebook(UUID, "Project Alpha", None)
    assert frontmatter_id(render_markdown(nb, "abc")) == UUID


@pytest.mark.parametrize("text", ["no frontmatter", "---\nsource: remarkable\n---\n", "", "---\nunterminated\n"])
def test_frontmatter_id_is_none_without_an_id(text):
    assert frontmatter_id(text) is None


# --------------------------------------------------------------------------
# Filesystem helpers
# --------------------------------------------------------------------------


def test_atomic_write_leaves_no_temporary_file(tmp_path):
    target = tmp_path / "nested" / "note.md"
    write_text_atomic(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
    write_text_atomic(target, "goodbye")
    assert target.read_text(encoding="utf-8") == "goodbye"
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["note.md"]


def test_replace_tree_swaps_content_and_cleans_up(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "new.txt").write_text("new")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "old.txt").write_text("old")

    replace_tree(source, dest)

    assert (dest / "new.txt").read_text() == "new"
    assert not (dest / "old.txt").exists()
    assert not (tmp_path / "dest.rmos-new").exists()
    assert not (tmp_path / "dest.rmos-old").exists()


def test_prune_removes_only_our_own_renamed_notes(tmp_path):
    (tmp_path / "Old Name.md").write_text(f"---\nremarkable_id: {UUID}\n---\n", encoding="utf-8")
    (tmp_path / "New Name.md").write_text(f"---\nremarkable_id: {UUID}\n---\n", encoding="utf-8")
    (tmp_path / "Someone Else.md").write_text(f"---\nremarkable_id: {OTHER}\n---\n", encoding="utf-8")
    (tmp_path / "User Note.md").write_text("personal\n", encoding="utf-8")

    removed = prune_stale_notes(tmp_path, UUID, "New Name.md")

    assert [p.name for p in removed] == ["Old Name.md"]
    assert {p.name for p in tmp_path.glob("*.md")} == {"New Name.md", "Someone Else.md", "User Note.md"}


def test_fingerprint_skips_symlinks(tmp_path):
    (tmp_path / "real").write_text("data")
    before = fingerprint_tree(tmp_path)
    Path(tmp_path / "link").symlink_to(tmp_path / "real")
    assert fingerprint_tree(tmp_path) == before
