"""Functional tests for the device-side POSIX sh scripts.

They run against a temporary RMOS_STATE_DIR, so no tablet is required. The
scripts are the trust boundary between the on-device UI action and the desktop
client, so their UUID validation is tested rather than assumed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "remarkable"
UUID = "550e8400-e29b-41d4-a716-446655440000"
OTHER = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


def run(script, *args, state_dir):
    return subprocess.run(
        ["sh", str(SCRIPTS / script), *args],
        env={"PATH": "/usr/bin:/bin", "RMOS_STATE_DIR": str(state_dir)},
        text=True,
        capture_output=True,
    )


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path / "rmos"


def selected_lines(state_dir):
    text = (state_dir / "selected.txt").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line and not line.startswith("#")]


def test_select_creates_the_state_file_with_its_header(state_dir):
    assert run("rmos-select", UUID, state_dir=state_dir).returncode == 0
    text = (state_dir / "selected.txt").read_text(encoding="utf-8")
    assert text.startswith("# UUIDs selected for Obsidian export")
    assert selected_lines(state_dir) == [UUID]


def test_select_is_idempotent(state_dir):
    run("rmos-select", UUID, state_dir=state_dir)
    result = run("rmos-select", UUID, state_dir=state_dir)
    assert "already selected" in result.stdout
    assert selected_lines(state_dir) == [UUID]


def test_select_normalises_case_so_it_cannot_double_add(state_dir):
    run("rmos-select", UUID.upper(), state_dir=state_dir)
    run("rmos-select", UUID, state_dir=state_dir)
    assert selected_lines(state_dir) == [UUID]


def test_unselect_removes_only_the_named_uuid(state_dir):
    run("rmos-select", UUID, state_dir=state_dir)
    run("rmos-select", OTHER, state_dir=state_dir)
    assert run("rmos-unselect", UUID, state_dir=state_dir).returncode == 0
    assert selected_lines(state_dir) == [OTHER]


def test_unselect_on_a_missing_state_file_is_a_no_op(state_dir):
    assert run("rmos-unselect", UUID, state_dir=state_dir).returncode == 0
    assert not state_dir.exists()


def test_unselecting_the_last_uuid_keeps_the_header(state_dir):
    run("rmos-select", UUID, state_dir=state_dir)
    run("rmos-unselect", UUID, state_dir=state_dir)
    assert selected_lines(state_dir) == []
    assert (state_dir / "selected.txt").read_text(encoding="utf-8").startswith("#")


@pytest.mark.parametrize("script", ["rmos-select", "rmos-unselect"])
@pytest.mark.parametrize(
    "bad",
    [
        "../../../etc/passwd",
        "550e8400-e29b-41d4-a716-44665544000",
        "zzzzzzzz-e29b-41d4-a716-446655440000",
        "550e8400/e29b/41d4/a716/446655440000",
        "$(touch /tmp/rmos-pwned)",
        "",
    ],
)
def test_scripts_reject_anything_that_is_not_a_uuid(script, bad, state_dir):
    result = run(script, bad, state_dir=state_dir)
    assert result.returncode == 2
    assert not (state_dir / "selected.txt").exists()


@pytest.mark.parametrize("script", ["rmos-select", "rmos-unselect"])
def test_scripts_require_exactly_one_argument(script, state_dir):
    assert run(script, state_dir=state_dir).returncode == 2
    assert run(script, UUID, UUID, state_dir=state_dir).returncode == 2


def test_install_script_touches_nothing_under_xochitl(tmp_path):
    """The installer must stay confined to our own state and bin directories."""
    prefix = tmp_path / "bin"
    prefix.mkdir()
    state_dir = tmp_path / "state"
    xochitl = tmp_path / "xochitl"
    xochitl.mkdir()
    (xochitl / "canary").write_text("untouched", encoding="utf-8")

    result = subprocess.run(
        ["sh", str(SCRIPTS / "install.sh")],
        env={"PATH": "/usr/bin:/bin", "RMOS_PREFIX": str(prefix), "RMOS_STATE_DIR": str(state_dir)},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert (prefix / "rmos-select").exists()
    assert (prefix / "rmos-unselect").exists()
    assert (state_dir / "selected.txt").exists()
    assert (state_dir / "VERSION").exists()
    assert (xochitl / "canary").read_text(encoding="utf-8") == "untouched"


def test_install_is_idempotent_and_preserves_the_selection(tmp_path):
    prefix = tmp_path / "bin"
    prefix.mkdir()
    state_dir = tmp_path / "state"
    env = {"PATH": "/usr/bin:/bin", "RMOS_PREFIX": str(prefix), "RMOS_STATE_DIR": str(state_dir)}

    subprocess.run(["sh", str(SCRIPTS / "install.sh")], env=env, check=True, capture_output=True)
    run("rmos-select", UUID, state_dir=state_dir)
    subprocess.run(["sh", str(SCRIPTS / "install.sh")], env=env, check=True, capture_output=True)

    assert selected_lines(state_dir) == [UUID]


def test_uninstall_removes_scripts_but_keeps_state(tmp_path):
    prefix = tmp_path / "bin"
    prefix.mkdir()
    state_dir = tmp_path / "state"
    env = {"PATH": "/usr/bin:/bin", "RMOS_PREFIX": str(prefix), "RMOS_STATE_DIR": str(state_dir)}

    subprocess.run(["sh", str(SCRIPTS / "install.sh")], env=env, check=True, capture_output=True)
    run("rmos-select", UUID, state_dir=state_dir)
    subprocess.run(["sh", str(SCRIPTS / "uninstall.sh")], env=env, check=True, capture_output=True)

    assert not (prefix / "rmos-select").exists()
    assert not (prefix / "rmos-unselect").exists()
    assert selected_lines(state_dir) == [UUID]


def test_shellcheck_is_clean_when_available():
    if not shutil.which("shellcheck"):
        pytest.skip("shellcheck not installed")
    targets = ["install.sh", "uninstall.sh", "rmos-select", "rmos-unselect"]
    result = subprocess.run(
        ["shellcheck", "-s", "sh", *[str(SCRIPTS / t) for t in targets]],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout
