"""Sync-level tests against a fake tablet.

The SSH boundary is stubbed out, so everything below it - fingerprint
comparison, rename handling, collision handling, state updates and the
guarantee that nothing is ever deleted from the vault - is exercised for real
against a temporary vault on disk.
"""

import argparse
import dataclasses
import hashlib
import json
import sys

import pytest

from rmos import cli
from rmos.core import fingerprint_entries, frontmatter_id

UUID = "550e8400-e29b-41d4-a716-446655440000"
OTHER = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


class FakeSsh:
    hash_algo = "sha256"


class FakeDevice:
    """An in-memory stand-in for the notebook store on the tablet."""

    def __init__(self):
        self.documents = {}
        self.pulls = 0

    def add(self, uuid, name, files=None, modified="1700000000000"):
        self.documents[uuid] = {
            "name": name,
            "modified": modified,
            "files": dict(files or {f"{uuid}.metadata": b'{"visibleName": "x"}', f"{uuid}/1.rm": b"strokes"}),
        }

    def rename(self, uuid, name):
        document = self.documents[uuid]
        document["name"] = name
        # A rename rewrites .metadata on the device, so the bundle really changes.
        document["files"][f"{uuid}.metadata"] = json.dumps({"visibleName": name}).encode()

    # --- stand-ins for the cli's remote helpers -------------------------

    def metadata(self, _ssh, uuid):
        document = self.documents[uuid]
        return {"visibleName": document["name"], "lastModified": document["modified"]}

    def fingerprint(self, _ssh, uuid):
        files = self.documents[uuid]["files"]
        entries = [(rel, hashlib.sha256(data).hexdigest()) for rel, data in files.items()]
        return fingerprint_entries(entries, algo="sha256"), sorted(files)

    def pull(self, _ssh, uuid, dest):
        self.pulls += 1
        for rel, data in self.documents[uuid]["files"].items():
            path = dest / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


@pytest.fixture
def device(monkeypatch):
    fake = FakeDevice()
    monkeypatch.setattr(cli, "remote_metadata", fake.metadata)
    monkeypatch.setattr(cli, "remote_fingerprint", fake.fingerprint)
    monkeypatch.setattr(cli, "pull_bundle", fake.pull)
    return fake


@pytest.fixture
def cfg(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Sources" / "reMarkable").mkdir(parents=True)
    return cli.Config(vault=vault, source="Sources/reMarkable", state=tmp_path / "state.json")


def run_sync(cfg, device, selected, monkeypatch, *, dry_run=False, re_render=False):
    monkeypatch.setattr(cli, "read_selection", lambda _ssh, _cfg: list(selected))
    monkeypatch.setattr(cli, "Ssh", lambda *a, **k: FakeSsh())
    args = argparse.Namespace(dry_run=dry_run, re_render=re_render, verbose=False, batch=False, wait=0)
    return cli.cmd_sync(cfg, args)


def note_for(cfg, folder):
    return cfg.vault_source / folder / f"{folder}.md"


# --------------------------------------------------------------------------


def test_first_sync_creates_note_and_raw_bundle(cfg, device, monkeypatch):
    device.add(UUID, "Project Alpha")

    assert run_sync(cfg, device, [UUID], monkeypatch) == 0

    note = note_for(cfg, "Project Alpha")
    assert note.exists()
    assert frontmatter_id(note.read_text(encoding="utf-8")) == UUID
    assert (cfg.vault_source / "Project Alpha" / "raw" / f"{UUID}.metadata").exists()
    assert (cfg.vault_source / "Project Alpha" / "raw" / UUID / "1.rm").read_bytes() == b"strokes"


def test_state_records_identity_and_destination(cfg, device, monkeypatch):
    device.add(UUID, "Project Alpha")
    run_sync(cfg, device, [UUID], monkeypatch)

    entry = json.loads(cfg.state.read_text(encoding="utf-8"))["documents"][UUID]
    assert entry["visible_name"] == "Project Alpha"
    assert entry["destination"] == str(cfg.vault_source / "Project Alpha")
    assert entry["fingerprint"] == device.fingerprint(None, UUID)[0]


def test_second_sync_with_no_changes_transfers_and_writes_nothing(cfg, device, monkeypatch):
    device.add(UUID, "Project Alpha")
    run_sync(cfg, device, [UUID], monkeypatch)
    note = note_for(cfg, "Project Alpha")
    before = note.stat().st_mtime_ns
    pulls = device.pulls

    run_sync(cfg, device, [UUID], monkeypatch)

    assert device.pulls == pulls
    assert note.stat().st_mtime_ns == before


def test_edited_notebook_is_resynced(cfg, device, monkeypatch):
    device.add(UUID, "Project Alpha")
    run_sync(cfg, device, [UUID], monkeypatch)
    device.documents[UUID]["files"][f"{UUID}/1.rm"] = b"more strokes"

    run_sync(cfg, device, [UUID], monkeypatch)

    raw = cfg.vault_source / "Project Alpha" / "raw" / UUID / "1.rm"
    assert raw.read_bytes() == b"more strokes"


def test_rename_moves_the_folder_instead_of_duplicating_it(cfg, device, monkeypatch):
    device.add(UUID, "Project Alpha")
    run_sync(cfg, device, [UUID], monkeypatch)
    device.rename(UUID, "Project Beta")

    run_sync(cfg, device, [UUID], monkeypatch)

    assert not (cfg.vault_source / "Project Alpha").exists()
    assert note_for(cfg, "Project Beta").exists()
    assert not (cfg.vault_source / "Project Beta" / "Project Alpha.md").exists()
    assert sorted(p.name for p in cfg.vault_source.iterdir()) == ["Project Beta"]


def test_rename_preserves_the_synced_bundle(cfg, device, monkeypatch):
    device.add(UUID, "Project Alpha")
    run_sync(cfg, device, [UUID], monkeypatch)
    device.rename(UUID, "Project Beta")
    run_sync(cfg, device, [UUID], monkeypatch)

    assert (cfg.vault_source / "Project Beta" / "raw" / UUID / "1.rm").read_bytes() == b"strokes"


def test_rename_keeps_a_single_identity_in_state(cfg, device, monkeypatch):
    device.add(UUID, "Project Alpha")
    run_sync(cfg, device, [UUID], monkeypatch)
    device.rename(UUID, "Project Beta")
    run_sync(cfg, device, [UUID], monkeypatch)

    documents = json.loads(cfg.state.read_text(encoding="utf-8"))["documents"]
    assert list(documents) == [UUID]
    assert documents[UUID]["destination"] == str(cfg.vault_source / "Project Beta")


def test_two_notebooks_named_alike_do_not_clobber_each_other(cfg, device, monkeypatch):
    device.add(UUID, "Notes")
    device.add(OTHER, "Notes")

    run_sync(cfg, device, [UUID, OTHER], monkeypatch)

    folders = sorted(p.name for p in cfg.vault_source.iterdir())
    assert folders == ["Notes", f"Notes ({OTHER[:8]})"]
    assert frontmatter_id(note_for(cfg, "Notes").read_text(encoding="utf-8")) == UUID
    assert frontmatter_id(note_for(cfg, f"Notes ({OTHER[:8]})").read_text(encoding="utf-8")) == OTHER


def test_unselecting_never_deletes_from_the_vault(cfg, device, monkeypatch):
    device.add(UUID, "Project Alpha")
    device.add(OTHER, "Project Beta")
    run_sync(cfg, device, [UUID, OTHER], monkeypatch)

    run_sync(cfg, device, [UUID], monkeypatch)

    assert note_for(cfg, "Project Beta").exists()
    assert (cfg.vault_source / "Project Beta" / "raw" / OTHER / "1.rm").exists()


def test_dry_run_writes_nothing(cfg, device, monkeypatch):
    device.add(UUID, "Project Alpha")

    assert run_sync(cfg, device, [UUID], monkeypatch, dry_run=True) == 0

    assert list(cfg.vault_source.iterdir()) == []
    assert not cfg.state.exists()
    assert device.pulls == 0


def test_a_bundle_that_changes_mid_transfer_leaves_the_vault_untouched(cfg, device, monkeypatch):
    device.add(UUID, "Project Alpha")

    def racy_pull(_ssh, uuid, dest):
        # A complete, well-formed bundle - but not the one we fingerprinted.
        device.pull(_ssh, uuid, dest)
        (dest / f"{uuid}/1.rm").write_bytes(b"a stroke drawn mid-transfer")

    monkeypatch.setattr(cli, "pull_bundle", racy_pull)

    assert run_sync(cfg, device, [UUID], monkeypatch) == 1
    assert not (cfg.vault_source / "Project Alpha" / "raw").exists()
    assert not cfg.state.exists()


def test_a_racy_transfer_does_not_damage_an_already_synced_note(cfg, device, monkeypatch):
    device.add(UUID, "Project Alpha")
    run_sync(cfg, device, [UUID], monkeypatch)
    good = (cfg.vault_source / "Project Alpha" / "raw" / UUID / "1.rm").read_bytes()
    device.documents[UUID]["files"][f"{UUID}/1.rm"] = b"edited on the tablet"

    def racy_pull(_ssh, uuid, dest):
        device.pull(_ssh, uuid, dest)
        (dest / f"{uuid}/1.rm").write_bytes(b"edited again mid-transfer")

    monkeypatch.setattr(cli, "pull_bundle", racy_pull)

    assert run_sync(cfg, device, [UUID], monkeypatch) == 1
    assert (cfg.vault_source / "Project Alpha" / "raw" / UUID / "1.rm").read_bytes() == good


def test_one_broken_notebook_does_not_stop_the_others(cfg, device, monkeypatch):
    device.add(UUID, "Project Alpha")
    device.add(OTHER, "Project Beta")

    def flaky_metadata(_ssh, uuid):
        if uuid == UUID:
            raise cli.RmosError("metadata is unreadable")
        return device.metadata(_ssh, uuid)

    monkeypatch.setattr(cli, "remote_metadata", flaky_metadata)

    assert run_sync(cfg, device, [UUID, OTHER], monkeypatch) == 1
    assert note_for(cfg, "Project Beta").exists()


def test_state_survives_a_failure_partway_through(cfg, device, monkeypatch):
    device.add(UUID, "Project Alpha")
    device.add(OTHER, "Project Beta")

    def failing_pull(_ssh, uuid, dest):
        if uuid == OTHER:
            raise cli.RmosError("transfer died")
        device.pull(_ssh, uuid, dest)

    monkeypatch.setattr(cli, "pull_bundle", failing_pull)
    run_sync(cfg, device, [UUID, OTHER], monkeypatch)

    documents = json.loads(cfg.state.read_text(encoding="utf-8"))["documents"]
    assert list(documents) == [UUID]


def test_a_notebook_with_an_unusable_name_still_syncs(cfg, device, monkeypatch):
    device.add(UUID, "  ..  ")

    assert run_sync(cfg, device, [UUID], monkeypatch) == 0
    assert note_for(cfg, UUID).exists()


# --------------------------------------------------------------------------
# State file handling
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def renderer_cfg(cfg, script, extension="pdf"):
    return dataclasses.replace(
        cfg,
        render={
            "backend": "command",
            "command": [sys.executable, str(script), "{out}", "{name}"],
            "extension": extension,
        },
    )


@pytest.fixture
def good_renderer(tmp_path):
    script = tmp_path / "renderer.py"
    script.write_text(
        "import sys, pathlib\n"
        'pathlib.Path(sys.argv[1]).write_text("PDF of " + sys.argv[2])\n',
        encoding="utf-8",
    )
    return script


@pytest.fixture
def broken_renderer(tmp_path):
    script = tmp_path / "broken.py"
    script.write_text("import sys\nsys.stderr.write('no parser for this format\\n')\nsys.exit(1)\n", encoding="utf-8")
    return script


def test_render_produces_an_attachment_and_embeds_it(cfg, device, monkeypatch, good_renderer):
    device.add(UUID, "Project Alpha")
    rendered = renderer_cfg(cfg, good_renderer)

    assert run_sync(rendered, device, [UUID], monkeypatch) == 0

    attachment = cfg.vault_source / "Project Alpha" / "attachments" / "Project Alpha.pdf"
    assert attachment.read_text(encoding="utf-8") == "PDF of Project Alpha"
    assert "![[attachments/Project Alpha.pdf]]" in note_for(cfg, "Project Alpha").read_text(encoding="utf-8")


def test_render_state_is_recorded_so_it_does_not_rerun(cfg, device, monkeypatch, good_renderer):
    device.add(UUID, "Project Alpha")
    rendered = renderer_cfg(cfg, good_renderer)
    run_sync(rendered, device, [UUID], monkeypatch)
    attachment = cfg.vault_source / "Project Alpha" / "attachments" / "Project Alpha.pdf"
    before = attachment.stat().st_mtime_ns

    run_sync(rendered, device, [UUID], monkeypatch)

    assert attachment.stat().st_mtime_ns == before
    entry = json.loads(cfg.state.read_text(encoding="utf-8"))["documents"][UUID]
    assert entry["attachments"] == ["Project Alpha.pdf"]
    assert entry["render"].startswith("command:")


def test_enabling_a_renderer_later_does_not_re_download(cfg, device, monkeypatch, good_renderer):
    device.add(UUID, "Project Alpha")
    run_sync(cfg, device, [UUID], monkeypatch)
    pulls = device.pulls

    assert run_sync(renderer_cfg(cfg, good_renderer), device, [UUID], monkeypatch) == 0

    assert device.pulls == pulls, "content was unchanged; only the rendering was stale"
    assert (cfg.vault_source / "Project Alpha" / "attachments" / "Project Alpha.pdf").exists()


def test_re_render_flag_forces_a_render_without_transferring(cfg, device, monkeypatch, good_renderer):
    device.add(UUID, "Project Alpha")
    rendered = renderer_cfg(cfg, good_renderer)
    run_sync(rendered, device, [UUID], monkeypatch)
    attachment = cfg.vault_source / "Project Alpha" / "attachments" / "Project Alpha.pdf"
    attachment.unlink()
    pulls = device.pulls

    assert run_sync(rendered, device, [UUID], monkeypatch, re_render=True) == 0

    assert attachment.exists()
    assert device.pulls == pulls


def test_a_failing_renderer_still_imports_the_raw_bundle(cfg, device, monkeypatch, broken_renderer, capsys):
    device.add(UUID, "Project Alpha")

    assert run_sync(renderer_cfg(cfg, broken_renderer), device, [UUID], monkeypatch) == 0

    assert (cfg.vault_source / "Project Alpha" / "raw" / UUID / "1.rm").exists()
    note = note_for(cfg, "Project Alpha").read_text(encoding="utf-8")
    assert "![[attachments/" not in note
    assert "rendering is not enabled yet" in note
    assert "no parser for this format" in capsys.readouterr().err


def test_a_failing_renderer_is_not_retried_on_every_sync(cfg, device, monkeypatch, broken_renderer):
    device.add(UUID, "Project Alpha")
    broken = renderer_cfg(cfg, broken_renderer)
    run_sync(broken, device, [UUID], monkeypatch)

    entry = json.loads(cfg.state.read_text(encoding="utf-8"))["documents"][UUID]
    assert entry["render"].startswith("failed:command:")

    pulls = device.pulls
    run_sync(broken, device, [UUID], monkeypatch)
    assert device.pulls == pulls


def test_fixing_the_renderer_config_triggers_a_retry(cfg, device, monkeypatch, broken_renderer, good_renderer):
    device.add(UUID, "Project Alpha")
    run_sync(renderer_cfg(cfg, broken_renderer), device, [UUID], monkeypatch)

    assert run_sync(renderer_cfg(cfg, good_renderer), device, [UUID], monkeypatch) == 0
    assert (cfg.vault_source / "Project Alpha" / "attachments" / "Project Alpha.pdf").exists()


def test_rename_moves_the_attachment_and_removes_the_old_one(cfg, device, monkeypatch, good_renderer):
    device.add(UUID, "Project Alpha")
    rendered = renderer_cfg(cfg, good_renderer)
    run_sync(rendered, device, [UUID], monkeypatch)
    device.rename(UUID, "Project Beta")

    run_sync(rendered, device, [UUID], monkeypatch)

    attachments = cfg.vault_source / "Project Beta" / "attachments"
    assert [p.name for p in attachments.iterdir()] == ["Project Beta.pdf"]
    assert "![[attachments/Project Beta.pdf]]" in note_for(cfg, "Project Beta").read_text(encoding="utf-8")


def test_turning_the_renderer_off_keeps_the_existing_attachment_linked(cfg, device, monkeypatch, good_renderer):
    device.add(UUID, "Project Alpha")
    run_sync(renderer_cfg(cfg, good_renderer), device, [UUID], monkeypatch)

    assert run_sync(cfg, device, [UUID], monkeypatch) == 0

    attachment = cfg.vault_source / "Project Alpha" / "attachments" / "Project Alpha.pdf"
    assert attachment.exists(), "an attachment we produced is never deleted"
    assert "![[attachments/Project Alpha.pdf]]" in note_for(cfg, "Project Alpha").read_text(encoding="utf-8")


def test_a_deleted_attachment_drops_out_of_the_note(cfg, device, monkeypatch, good_renderer):
    device.add(UUID, "Project Alpha")
    run_sync(renderer_cfg(cfg, good_renderer), device, [UUID], monkeypatch)
    (cfg.vault_source / "Project Alpha" / "attachments" / "Project Alpha.pdf").unlink()

    run_sync(cfg, device, [UUID], monkeypatch)

    assert "![[attachments/" not in note_for(cfg, "Project Alpha").read_text(encoding="utf-8")


def test_a_misconfigured_renderer_fails_the_command_not_the_vault(cfg, device, monkeypatch):
    device.add(UUID, "Project Alpha")
    broken = dataclasses.replace(cfg, render={"backend": "nonsense"})

    monkeypatch.setattr(cli, "read_selection", lambda _ssh, _cfg: [UUID])
    monkeypatch.setattr(cli, "Ssh", lambda *a, **k: FakeSsh())
    with pytest.raises(cli.RmosError, match="Unknown"):
        cli.cmd_sync(broken, argparse.Namespace(dry_run=False, re_render=False, verbose=False, batch=False, wait=0))

    assert list(cfg.vault_source.iterdir()) == []


# --------------------------------------------------------------------------
# State file handling
# --------------------------------------------------------------------------


def test_missing_state_file_starts_empty(tmp_path):
    assert cli.load_state(tmp_path / "absent.json") == {"documents": {}}


def test_corrupt_state_file_is_reported_not_ignored(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ broken", encoding="utf-8")
    with pytest.raises(cli.RmosError, match="Corrupt state file"):
        cli.load_state(path)


def test_state_round_trips(tmp_path):
    path = tmp_path / "nested" / "state.json"
    cli.save_state(path, {"documents": {UUID: {"fingerprint": "abc"}}})
    assert cli.load_state(path)["documents"][UUID]["fingerprint"] == "abc"
    assert not path.with_suffix(".json.tmp").exists()
