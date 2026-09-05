"""Tests for format inspection and the pluggable renderer."""

import json
import re
import sys

import pytest

from rmos.render import (
    CommandRenderer,
    NullRenderer,
    RenderError,
    build_renderer,
    detect_rm_version,
    inspect_bundle,
)

UUID = "550e8400-e29b-41d4-a716-446655440000"

HEADERS = {
    "v1/v2": b"reMarkable lines with selections and layers",
    "v3": b"reMarkable .lines file, version=3          ",
    "v5": b"reMarkable .lines file, version=5          ",
    "v6": b"reMarkable .lines file, version=6          ",
}


def write_rm(path, version, payload=b"\x00\x01\x02"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(HEADERS[version] + payload)
    return path


# --------------------------------------------------------------------------
# Format detection
# --------------------------------------------------------------------------


@pytest.mark.parametrize("version", sorted(HEADERS))
def test_every_known_header_is_recognised(tmp_path, version):
    assert detect_rm_version(write_rm(tmp_path / "page.rm", version)) == version


def test_unknown_header_is_reported_rather_than_guessed(tmp_path):
    path = tmp_path / "page.rm"
    path.write_bytes(b"reMarkable .lines file, version=9          ")
    assert detect_rm_version(path) is None


def test_detection_survives_a_truncated_or_missing_file(tmp_path):
    (tmp_path / "short.rm").write_bytes(b"reMark")
    assert detect_rm_version(tmp_path / "short.rm") is None
    assert detect_rm_version(tmp_path / "absent.rm") is None


# --------------------------------------------------------------------------
# Bundle inspection
# --------------------------------------------------------------------------


def make_bundle(tmp_path, version="v5", pages=2, content=None):
    raw = tmp_path / "raw"
    raw.mkdir()
    for index in range(pages):
        write_rm(raw / UUID / f"page{index}.rm", version)
    (raw / f"{UUID}.metadata").write_text('{"visibleName": "Project Alpha"}', encoding="utf-8")
    if content is not None:
        (raw / f"{UUID}.content").write_text(json.dumps(content), encoding="utf-8")
    return raw


def test_inspect_counts_pages_and_formats(tmp_path):
    raw = make_bundle(tmp_path, version="v5", pages=3, content={"fileType": "notebook", "pageCount": 3})
    report = inspect_bundle(raw, UUID)

    assert report.file_type == "notebook"
    assert report.page_count == 3
    assert report.rm_files == 3
    assert report.versions == {"v5": 3}
    assert report.unknown_headers == []
    assert report.renderable is True
    assert report.total_bytes > 0


def test_inspect_reads_page_count_from_a_page_list(tmp_path):
    raw = make_bundle(tmp_path, pages=2, content={"pages": ["a", "b", "c"]})
    assert inspect_bundle(raw, UUID).page_count == 3


def test_inspect_tolerates_a_missing_or_corrupt_content_file(tmp_path):
    raw = make_bundle(tmp_path, pages=1)
    assert inspect_bundle(raw, UUID).page_count is None

    (raw / f"{UUID}.content").write_text("{ not json", encoding="utf-8")
    report = inspect_bundle(raw, UUID)
    assert report.page_count is None
    assert report.rm_files == 1


def test_inspect_flags_unrecognised_files_and_refuses_to_call_them_renderable(tmp_path):
    raw = make_bundle(tmp_path, version="v5", pages=1)
    (raw / UUID / "odd.rm").write_bytes(b"something else entirely")

    report = inspect_bundle(raw, UUID)
    assert report.versions == {"v5": 1}
    assert report.unknown_headers == [f"{UUID}/odd.rm"]
    assert report.renderable is False


def test_inspect_reports_mixed_formats(tmp_path):
    raw = make_bundle(tmp_path, version="v5", pages=1)
    write_rm(raw / UUID / "old.rm", "v3")
    assert inspect_bundle(raw, UUID).versions == {"v3": 1, "v5": 1}


def test_a_bundle_with_no_stroke_data_is_not_renderable(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / f"{UUID}.metadata").write_text("{}", encoding="utf-8")
    report = inspect_bundle(raw, UUID)
    assert report.rm_files == 0
    assert report.renderable is False


# --------------------------------------------------------------------------
# Renderer construction
# --------------------------------------------------------------------------


def test_default_backend_renders_nothing(tmp_path):
    renderer = build_renderer({})
    assert isinstance(renderer, NullRenderer)
    assert renderer.render(raw=tmp_path, uuid=UUID, visible_name="A", out_dir=tmp_path) is None


@pytest.mark.parametrize("settings", [{"backend": "none"}, {"backend": ""}, {}])
def test_null_backend_spellings(settings):
    assert build_renderer(settings).name == "none"


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="Unknown"):
        build_renderer({"backend": "magic"})


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"backend": "command"}, "list of strings"),
        ({"backend": "command", "command": "not a list"}, "list of strings"),
        ({"backend": "command", "command": []}, "must not be empty"),
        ({"backend": "command", "command": ["tool", "--in", "{raw}"]}, "{out} placeholder"),
    ],
)
def test_command_backend_validates_its_configuration(settings, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        build_renderer(settings)


def test_signature_changes_when_the_command_or_extension_changes():
    base = CommandRenderer(["tool", "{out}"])
    assert base.signature == CommandRenderer(["tool", "{out}"]).signature
    assert base.signature != CommandRenderer(["other", "{out}"]).signature
    assert base.signature != CommandRenderer(["tool", "{out}"], extension="png").signature


def test_null_signature_is_stable():
    assert NullRenderer().signature == "none"
    assert NullRenderer().signature != CommandRenderer(["tool", "{out}"]).signature


# --------------------------------------------------------------------------
# Command renderer execution
# --------------------------------------------------------------------------


@pytest.fixture
def helper(tmp_path):
    """A stand-in render tool whose behaviour each test chooses."""

    def make(body):
        script = tmp_path / "helper.py"
        script.write_text("import sys, json, pathlib\n" + body, encoding="utf-8")
        return script

    return make


def run_render(renderer, tmp_path, name="Project Alpha"):
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    return renderer.render(raw=raw, uuid=UUID, visible_name=name, out_dir=tmp_path / "attachments")


def test_successful_render_names_the_attachment_after_the_notebook(tmp_path, helper):
    script = helper('pathlib.Path(sys.argv[1]).write_bytes(b"%PDF-1.4 fake")\n')
    renderer = CommandRenderer([sys.executable, str(script), "{out}"])

    produced = run_render(renderer, tmp_path)

    assert produced.name == "Project Alpha.pdf"
    assert produced.read_bytes() == b"%PDF-1.4 fake"
    assert produced.parent.name == "attachments"


def test_render_uses_the_configured_extension(tmp_path, helper):
    script = helper('pathlib.Path(sys.argv[1]).write_bytes(b"png")\n')
    renderer = CommandRenderer([sys.executable, str(script), "{out}"], extension="png")
    assert run_render(renderer, tmp_path).name == "Project Alpha.png"


def test_render_substitutes_every_placeholder(tmp_path, helper):
    script = helper(
        "out = sys.argv[1]\n"
        'pathlib.Path(out).write_text(json.dumps({"raw": sys.argv[2], "uuid": sys.argv[3], "name": sys.argv[4]}))\n'
    )
    renderer = CommandRenderer([sys.executable, str(script), "{out}", "{raw}", "{uuid}", "{name}"])

    produced = run_render(renderer, tmp_path, name="Project Alpha")
    payload = json.loads(produced.read_text(encoding="utf-8"))

    assert payload["raw"] == str(tmp_path / "raw")
    assert payload["uuid"] == UUID
    assert payload["name"] == "Project Alpha"


def test_an_unsafe_notebook_name_cannot_escape_the_attachments_folder(tmp_path, helper):
    script = helper('pathlib.Path(sys.argv[1]).write_bytes(b"pdf")\n')
    renderer = CommandRenderer([sys.executable, str(script), "{out}"])

    produced = run_render(renderer, tmp_path, name="../../escape")

    assert produced.parent == tmp_path / "attachments"
    assert produced.name == "_.._escape.pdf"
    assert "/" not in produced.name


def test_a_failing_command_raises_and_leaves_no_partial_file(tmp_path, helper):
    script = helper('sys.stderr.write("boom\\n"); sys.exit(3)\n')
    renderer = CommandRenderer([sys.executable, str(script), "{out}"])

    with pytest.raises(RenderError, match="exit 3"):
        run_render(renderer, tmp_path)

    assert list((tmp_path / "attachments").iterdir()) == []


def test_a_command_that_writes_nothing_is_an_error(tmp_path, helper):
    script = helper("pass\n")
    renderer = CommandRenderer([sys.executable, str(script), "{out}"])
    with pytest.raises(RenderError, match="no output file"):
        run_render(renderer, tmp_path)


def test_an_empty_output_file_is_an_error(tmp_path, helper):
    script = helper('pathlib.Path(sys.argv[1]).write_bytes(b"")\n')
    renderer = CommandRenderer([sys.executable, str(script), "{out}"])
    with pytest.raises(RenderError, match="no output file"):
        run_render(renderer, tmp_path)
    assert list((tmp_path / "attachments").iterdir()) == []


def test_a_missing_executable_is_reported_before_running(tmp_path):
    renderer = CommandRenderer(["rmos-definitely-not-installed", "{out}"])
    with pytest.raises(RenderError, match="not found on PATH"):
        run_render(renderer, tmp_path)


def test_a_hanging_command_is_killed(tmp_path, helper):
    script = helper("import time; time.sleep(30)\n")
    renderer = CommandRenderer([sys.executable, str(script), "{out}"], timeout=1)
    with pytest.raises(RenderError, match="timed out"):
        run_render(renderer, tmp_path)


def test_the_command_is_not_run_through_a_shell(tmp_path, helper):
    """Shell metacharacters in a notebook name must not be interpreted."""
    script = helper('pathlib.Path(sys.argv[1]).write_text(sys.argv[2])\n')
    renderer = CommandRenderer([sys.executable, str(script), "{out}", "{name}"])

    produced = run_render(renderer, tmp_path, name="; touch /tmp/rmos-pwned #")

    assert produced.read_text(encoding="utf-8") == "; touch /tmp/rmos-pwned #"
