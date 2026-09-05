"""Tests for the Omarchy plugin's manifest and bin/ scripts.

The QML side runs these as subprocesses and parses stdout with JSON.parse, so
each one must print exactly one line of JSON and nothing else - including when
it fails. These tests hold that contract without needing a shell, a GUI or a
tablet.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "omarchy"
BIN = PLUGIN / "bin"
SCRIPTS = sorted(p for p in BIN.iterdir() if p.name != "_common.sh")


def run(script, *args, env=None, stdin=""):
    return subprocess.run(
        [str(BIN / script), *args],
        text=True,
        capture_output=True,
        input=stdin,
        env={**os.environ, **(env or {})},
    )


def one_object(result):
    """Assert stdout is a single line of JSON, and return it."""
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected one JSON line, got {lines!r} (stderr: {result.stderr!r})"
    return json.loads(lines[0])


@pytest.fixture
def plugin_config(tmp_path):
    """A plugin config pointing at hardware and a host that do not exist.

    192.0.2.1 is TEST-NET-1, reserved and unroutable, so these tests behave the
    same whether or not a real tablet happens to be plugged in.
    """
    path = tmp_path / "remarkable-sync.json"
    path.write_text(
        json.dumps({"vendor": "dead", "product": "beef", "host": "192.0.2.1", "connectTimeout": 1}),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def fake_rmos(tmp_path):
    """A stand-in rmos that records its arguments and prints canned JSON."""
    log = tmp_path / "calls.log"
    script = tmp_path / "rmos"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {log}\n'
        'printf \'{"ok":true,"argv":"%s"}\\n\' "$*"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, log


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------


def manifest():
    return json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))


def test_the_manifest_declares_what_omarchy_requires():
    data = manifest()
    assert data["schemaVersion"] == 1
    for field in ("id", "name", "version", "kinds", "entryPoints"):
        assert data.get(field), f"manifest missing {field}"
    assert data["kinds"], "kinds must be a non-empty array"


def test_every_entry_point_is_a_relative_path_that_exists():
    for kind, entry in manifest()["entryPoints"].items():
        assert not entry.startswith("/"), f"{kind} entry point must be relative"
        assert ".." not in entry, f"{kind} entry point must not escape the plugin"
        assert (PLUGIN / entry).is_file(), f"{kind} entry point {entry} does not exist"


def test_each_declared_kind_has_its_entry_point():
    data = manifest()
    required = {"bar-widget": "barWidget", "overlay": "overlay"}
    for kind in data["kinds"]:
        assert required[kind] in data["entryPoints"], f"kind {kind} needs entryPoints.{required[kind]}"


def test_the_bar_widget_section_is_one_omarchy_accepts():
    assert manifest()["barWidget"]["defaultSection"] in ("left", "center", "right")


def test_omarchy_accepts_the_plugin():
    if not shutil.which("omarchy"):
        pytest.skip("omarchy not installed")
    result = subprocess.run(["omarchy", "plugin", "validate", str(PLUGIN)], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


# --------------------------------------------------------------------------
# The JSON contract
# --------------------------------------------------------------------------


def test_every_script_is_executable():
    for script in SCRIPTS:
        assert os.access(script, os.X_OK), f"{script.name} is not executable"


def test_probe_reports_absence_rather_than_failing(plugin_config):
    result = run("rmos-probe", env={"RMOS_PLUGIN_CONFIG": str(plugin_config)})
    assert result.returncode == 0
    assert one_object(result) == {"connected": False}


def test_probe_needs_no_ssh_and_no_config_file(tmp_path):
    """The bar widget polls this; it must cost nothing and never hang."""
    result = run("rmos-probe", env={"RMOS_PLUGIN_CONFIG": str(tmp_path / "absent.json")})
    assert result.returncode == 0
    assert "connected" in one_object(result)


def test_probe_does_not_shell_out_to_ssh_or_ping():
    text = (BIN / "rmos-probe").read_text(encoding="utf-8")
    for command in ("ssh ", "ping ", "rmos "):
        assert command not in text.replace("rmos-probe", ""), f"probe must not run {command.strip()}"


@pytest.mark.parametrize("script", ["rmos-report", "rmos-catalog", "rmos-run"])
def test_wrappers_pass_rmos_output_straight_through(script, plugin_config, fake_rmos):
    rmos, _ = fake_rmos
    plugin_config.write_text(json.dumps({"rmosPath": str(rmos)}), encoding="utf-8")

    payload = one_object(run(script, env={"RMOS_PLUGIN_CONFIG": str(plugin_config)}))

    assert payload["ok"] is True


@pytest.mark.parametrize(
    ("script", "expected"),
    [("rmos-report", "status"), ("rmos-catalog", "index"), ("rmos-run", "sync")],
)
def test_each_wrapper_calls_the_right_command_in_json_batch_mode(script, expected, plugin_config, fake_rmos):
    rmos, log = fake_rmos
    plugin_config.write_text(json.dumps({"rmosPath": str(rmos)}), encoding="utf-8")

    run(script, env={"RMOS_PLUGIN_CONFIG": str(plugin_config)})

    call = log.read_text(encoding="utf-8").strip()
    assert "--json" in call
    assert "--batch" in call, "an unattended run must never block on a prompt"
    assert expected in call


def test_extra_arguments_reach_rmos(plugin_config, fake_rmos):
    rmos, log = fake_rmos
    plugin_config.write_text(json.dumps({"rmosPath": str(rmos)}), encoding="utf-8")

    run("rmos-run", "--dry-run", env={"RMOS_PLUGIN_CONFIG": str(plugin_config)})

    assert "--dry-run" in log.read_text(encoding="utf-8")


def test_a_missing_rmos_is_explained_not_silent(plugin_config, tmp_path):
    plugin_config.write_text(json.dumps({"rmosPath": str(tmp_path / "nope")}), encoding="utf-8")

    payload = one_object(run("rmos-report", env={"RMOS_PLUGIN_CONFIG": str(plugin_config)}))

    assert "error" in payload
    assert "rmosPath" in payload["error"]


def test_the_helper_finds_a_checkout_virtualenv(plugin_config, tmp_path):
    """The comment promised this fallback long before the code did."""
    plugin_config.write_text(json.dumps({"vendor": "dead", "product": "beef"}), encoding="utf-8")
    venv_rmos = BIN.resolve().parents[1] / ".venv" / "bin" / "rmos"
    if not venv_rmos.exists():
        pytest.skip("no virtualenv in this checkout")

    payload = one_object(run("rmos-report", env={
        "RMOS_PLUGIN_CONFIG": str(plugin_config),
        "PATH": "/usr/bin:/bin",
    }))

    assert "is not on PATH" not in str(payload.get("error", "")), (
        "should have fallen back to the checkout's virtualenv"
    )


def test_an_rmos_failure_becomes_an_error_field(plugin_config, tmp_path):
    failing = tmp_path / "rmos"
    failing.write_text("#!/bin/sh\necho 'tablet not reachable' >&2\nexit 1\n", encoding="utf-8")
    failing.chmod(0o755)
    plugin_config.write_text(json.dumps({"rmosPath": str(failing)}), encoding="utf-8")

    payload = one_object(run("rmos-report", env={"RMOS_PLUGIN_CONFIG": str(plugin_config)}))

    assert payload["error"] == "tablet not reachable"


# --------------------------------------------------------------------------
# Applying a selection
# --------------------------------------------------------------------------


def test_apply_writes_tags_through_rmos_config(plugin_config, fake_rmos):
    rmos, log = fake_rmos
    plugin_config.write_text(json.dumps({"rmosPath": str(rmos)}), encoding="utf-8")

    payload = one_object(run("rmos-apply", "--tags", '["obsidian","sync"]',
                             env={"RMOS_PLUGIN_CONFIG": str(plugin_config)}))

    assert payload["ok"] is True
    assert payload["tags"] == ["obsidian", "sync"]
    assert "config set selection.tags" in log.read_text(encoding="utf-8")


def test_apply_selects_and_unselects_individual_notebooks(plugin_config, fake_rmos):
    rmos, log = fake_rmos
    plugin_config.write_text(json.dumps({"rmosPath": str(rmos)}), encoding="utf-8")
    a = "550e8400-e29b-41d4-a716-446655440000"
    b = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"

    payload = one_object(run("rmos-apply", "--select", a, "--unselect", b,
                             env={"RMOS_PLUGIN_CONFIG": str(plugin_config)}))

    calls = log.read_text(encoding="utf-8")
    assert payload["selected"] == [a]
    assert payload["unselected"] == [b]
    assert f"select {a}" in calls
    assert f"unselect {b}" in calls


def test_apply_with_nothing_to_do_is_still_valid_json(plugin_config, fake_rmos):
    rmos, _ = fake_rmos
    plugin_config.write_text(json.dumps({"rmosPath": str(rmos)}), encoding="utf-8")

    payload = one_object(run("rmos-apply", env={"RMOS_PLUGIN_CONFIG": str(plugin_config)}))

    assert payload == {"ok": True, "tags": None, "selected": [], "unselected": [], "failed": []}


def test_apply_rejects_tags_that_are_not_a_json_string_array(plugin_config, fake_rmos):
    rmos, log = fake_rmos
    plugin_config.write_text(json.dumps({"rmosPath": str(rmos)}), encoding="utf-8")

    payload = one_object(run("rmos-apply", "--tags", '"just-a-string"',
                             env={"RMOS_PLUGIN_CONFIG": str(plugin_config)}))

    assert "error" in payload
    assert not log.exists() or "config set" not in log.read_text(encoding="utf-8")


def test_apply_reports_a_failed_selection_rather_than_claiming_success(plugin_config, tmp_path):
    failing = tmp_path / "rmos"
    failing.write_text('#!/bin/sh\ncase "$*" in *select*) exit 1 ;; esac\necho "{}"\n', encoding="utf-8")
    failing.chmod(0o755)
    plugin_config.write_text(json.dumps({"rmosPath": str(failing)}), encoding="utf-8")

    payload = one_object(run("rmos-apply", "--select", "550e8400-e29b-41d4-a716-446655440000",
                             env={"RMOS_PLUGIN_CONFIG": str(plugin_config)}))

    assert payload["ok"] is False
    assert payload["failed"]


def test_apply_refuses_an_unknown_option(plugin_config):
    payload = one_object(run("rmos-apply", "--wipe-everything",
                             env={"RMOS_PLUGIN_CONFIG": str(plugin_config)}))
    assert "error" in payload


# --------------------------------------------------------------------------
# Pairing
# --------------------------------------------------------------------------


def test_the_password_is_never_taken_as_an_argument():
    """argv is visible to anyone who can run `ps`; the password comes on stdin."""
    text = (BIN / "rmos-pair").read_text(encoding="utf-8")
    assert "--password" not in text
    assert "read -r password" in text


def test_the_password_is_not_written_to_disk():
    text = (BIN / "rmos-pair").read_text(encoding="utf-8")
    askpass_line = next(line for line in text.splitlines() if "printf '#!/bin/sh" in line)
    assert "$RMOS_PW" in askpass_line, "the helper must read the value at run time"
    assert "$password" not in askpass_line, "the password itself must not be written into the helper"


def test_pairing_without_a_password_says_where_to_find_it(tmp_path, plugin_config):
    result = run(
        "rmos-pair",
        env={
            "RMOS_PLUGIN_CONFIG": str(plugin_config),
            "RMOS_KEY": str(tmp_path / "absent_key"),
            "HOME": str(tmp_path),
        },
        stdin="",
    )
    payload = one_object(result)
    assert "error" in payload
    assert "Settings" in payload["error"], "tell the user where the password is"


def test_the_check_asks_the_way_rmos_connects_not_with_an_explicit_key():
    """A check that passes -i would report success while rmos still failed.

    rmos finds the key through ~/.ssh/config; it never names one on the
    command line. So the check has to connect the same way, or it lies.
    """
    text = (BIN / "rmos-pair").read_text(encoding="utf-8")
    body = text[text.index("can_connect()"):text.index("key_accepted()")]
    assert "-i " not in body, "can_connect must not name a key; that is not how rmos connects"
    assert "$target" in body


def test_an_existing_key_is_adopted_without_asking_for_a_password():
    """Someone may have run ssh-copy-id by hand; that needs no password again."""
    text = (BIN / "rmos-pair").read_text(encoding="utf-8")
    adopt = text.index("if key_accepted; then")
    prompt = text.index("read -r password")
    assert adopt < prompt, "adopting an existing key must come before demanding a password"
    assert "adopted_existing_key" in text


def test_writing_the_ssh_config_block_is_idempotent():
    text = (BIN / "rmos-pair").read_text(encoding="utf-8")
    block = text[text.index("ensure_ssh_config() {"):text.index("if (( check_only ))")]
    assert 'grep -qF "$BEGIN_MARK"' in block, "must not append the block twice"


def test_pair_check_answers_without_a_password(tmp_path, plugin_config):
    payload = one_object(run("rmos-pair", "--check", env={
        "RMOS_PLUGIN_CONFIG": str(plugin_config),
        "RMOS_KEY": str(tmp_path / "absent_key"),
    }))
    assert payload["paired"] is False


def test_unpair_fences_its_block_so_it_can_be_removed_cleanly(tmp_path, plugin_config):
    ssh_config = tmp_path / "config"
    ssh_config.write_text(
        "Host example\n    User me\n\n"
        "# >>> rmos (remarkable-sync) >>>\n"
        "Host 10.11.99.1\n    IdentityFile /k\n"
        "# <<< rmos (remarkable-sync) <<<\n"
        "Host other\n    User you\n",
        encoding="utf-8",
    )

    payload = one_object(run("rmos-unpair", env={
        "RMOS_PLUGIN_CONFIG": str(plugin_config),
        "RMOS_SSH_CONFIG": str(ssh_config),
        "RMOS_KEY": str(tmp_path / "absent_key"),
    }))

    assert payload["removed_from_ssh_config"] is True
    remaining = ssh_config.read_text(encoding="utf-8")
    assert "10.11.99.1" not in remaining
    assert "Host example" in remaining, "everything around our block must survive"
    assert "Host other" in remaining


# --------------------------------------------------------------------------
# Shell hygiene
# --------------------------------------------------------------------------


def test_shellcheck_is_clean_when_available():
    if not shutil.which("shellcheck"):
        pytest.skip("shellcheck not installed")
    targets = [str(s) for s in SCRIPTS] + [str(PLUGIN / "install.sh"), str(PLUGIN / "uninstall.sh")]
    result = subprocess.run(["shellcheck", "-s", "bash", *targets], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout


# --------------------------------------------------------------------------
# Glyphs
# --------------------------------------------------------------------------
#
# A literal Nerd Font character in source is one careless round trip away from
# becoming an empty string, and an empty glyph renders as a hole rather than an
# error - which is exactly how this was first noticed. Escapes cannot be lost
# that way, and these tests stop a literal creeping back in.

GLYPH_SLOT = re.compile(r'^\s*(?:text|glyph):\s*"((?:[^"\\]|\\.)*)"', re.MULTILINE)


def qml_files():
    return sorted(PLUGIN.glob("*.qml"))


def test_no_glyph_slot_is_an_empty_string_where_an_icon_belongs():
    for path in qml_files():
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped in ('text: ""', 'glyph: ""') and "property" not in stripped:
                raise AssertionError(f"{path.name}:{line_number} has an empty glyph, which renders as a hole")


def test_icons_are_written_as_escapes_not_literal_characters():
    for path in qml_files():
        for raw in GLYPH_SLOT.findall(path.read_text(encoding="utf-8")):
            for char in raw:
                assert not (0xE000 <= ord(char) <= 0xF8FF), (
                    f"{path.name} carries a literal private-use glyph {hex(ord(char))}; "
                    "write it as \\uXXXX so it cannot be silently lost"
                )


def test_every_icon_exists_in_the_font_the_bar_uses():
    """A codepoint the font lacks renders as tofu or nothing at all."""
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        pytest.skip("fontTools not installed")

    found = subprocess.run(["fc-match", "-f", "%{file}", "monospace"], text=True, capture_output=True)
    if found.returncode != 0 or not found.stdout.strip():
        pytest.skip("fontconfig could not resolve a monospace font")

    font = TTFont(found.stdout.strip(), fontNumber=0)
    covered = set()
    for table in font["cmap"].tables:
        covered |= set(table.cmap.keys())

    used = set()
    for path in qml_files():
        for raw in GLYPH_SLOT.findall(path.read_text(encoding="utf-8")):
            for match in re.finditer(r"\\u([0-9a-fA-F]{4})", raw):
                used.add(int(match.group(1), 16))

    assert used, "expected the widget to use at least one icon"
    missing = sorted(cp for cp in used if cp not in covered)
    assert not missing, f"font lacks: {[hex(c) for c in missing]}"
