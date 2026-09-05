"""Tests for the attach-triggered sync installer.

The rendered udev rule and systemd unit are checked with the system's own
validators where available, so a syntax mistake fails here rather than
silently never firing.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

USB = Path(__file__).resolve().parents[1] / "desktop" / "usb"
INSTALL = USB / "install-usb-sync.sh"


@pytest.fixture
def fake_rmos(tmp_path):
    path = tmp_path / "rmos"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def dry_run(fake_rmos, *extra, expect_ok=True):
    result = subprocess.run(
        [
            "sh",
            str(INSTALL),
            "--dry-run",
            "--vendor", "04b3",
            "--product", "4010",
            "--rmos", str(fake_rmos),
            *extra,
        ],
        text=True,
        capture_output=True,
    )
    if expect_ok:
        assert result.returncode == 0, result.stderr
    return result


def split_sections(output):
    """Split the dry-run output into {path: body}."""
    sections = {}
    current = None
    for line in output.splitlines():
        if line.startswith("--- ") and line.endswith(" ---"):
            current = line[4:-4]
            sections[current] = []
        elif current:
            sections[current].append(line)
    return {k: "\n".join(v) + "\n" for k, v in sections.items()}


def unit_of(output):
    return next(body for path, body in split_sections(output).items() if path.endswith(".service"))


def rules_of(output):
    return next(body for path, body in split_sections(output).items() if path.endswith(".rules"))


# --------------------------------------------------------------------------


def test_dry_run_renders_both_files_and_writes_nothing(fake_rmos, tmp_path):
    sections = split_sections(dry_run(fake_rmos).stdout)
    assert len(sections) == 2
    assert any(p.endswith("rmos-sync.service") for p in sections)
    assert any(p.endswith("99-rmos.rules") for p in sections)
    assert not (tmp_path / "rmos-sync.service").exists()


def test_no_placeholder_survives_rendering(fake_rmos):
    output = dry_run(fake_rmos, "--notify").stdout
    assert "@" not in unit_of(output).replace("@REPO@", ""), unit_of(output)
    for token in ("@RMOS@", "@WAIT@", "@TIMEOUT@", "@NOTIFY@", "@VENDOR@", "@PRODUCT@", "@REPO@"):
        assert token not in output


def test_the_rule_matches_the_given_usb_ids(fake_rmos):
    rules = rules_of(dry_run(fake_rmos).stdout)
    assert 'ATTRS{idVendor}=="04b3"' in rules
    assert 'ATTRS{idProduct}=="4010"' in rules


def test_the_rule_activates_a_user_service_not_a_system_one(fake_rmos):
    """A system unit would run as root, without the user's config or keys."""
    rules = rules_of(dry_run(fake_rmos).stdout)
    assert "SYSTEMD_USER_WANTS" in rules
    assert "SYSTEMD_WANTS}" not in rules


def test_the_unit_runs_unattended_and_waits_for_the_tablet(fake_rmos):
    unit = unit_of(dry_run(fake_rmos).stdout)
    assert "--batch" in unit, "an attach-triggered run has no terminal to prompt on"
    assert "--wait 45" in unit
    assert str(fake_rmos) in unit
    assert "Type=oneshot" in unit


def test_wait_and_timeout_are_configurable(fake_rmos):
    unit = unit_of(dry_run(fake_rmos, "--wait", "90", "--timeout", "120").stdout)
    assert "--wait 90" in unit
    assert "TimeoutStartSec=120" in unit


def test_notifications_are_opt_in(fake_rmos):
    assert "notify-send" not in unit_of(dry_run(fake_rmos).stdout)
    assert "notify-send" in unit_of(dry_run(fake_rmos, "--notify").stdout)


def test_a_notification_failure_cannot_fail_the_sync(fake_rmos):
    """The ExecStopPost line is prefixed with '-' so it is best-effort."""
    unit = unit_of(dry_run(fake_rmos, "--notify").stdout)
    line = next(ln for ln in unit.splitlines() if "notify-send" in ln)
    assert line.startswith("ExecStopPost=-")


def test_a_checkout_virtualenv_is_found_without_being_told(tmp_path):
    """An editable install puts rmos in .venv, not on PATH. That is the normal
    state while developing, so it should not need --rmos."""
    result = subprocess.run(
        ["sh", str(INSTALL), "--dry-run", "--vendor", "04b3", "--product", "4010"],
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    venv_rmos = INSTALL.resolve().parents[2] / ".venv" / "bin" / "rmos"
    if venv_rmos.exists():
        assert str(venv_rmos) in unit_of(result.stdout)
    else:
        pytest.skip("no virtualenv in this checkout")


def test_a_missing_rmos_executable_is_reported(tmp_path):
    result = subprocess.run(
        ["sh", str(INSTALL), "--dry-run", "--vendor", "04b3", "--product", "4010",
         "--rmos", str(tmp_path / "absent")],
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "executable" in result.stderr.lower() or "find" in result.stderr.lower()


def test_unknown_options_are_rejected(fake_rmos):
    result = dry_run(fake_rmos, "--nonsense", expect_ok=False)
    assert result.returncode == 2


# --------------------------------------------------------------------------
# Validation with the system's own tools
# --------------------------------------------------------------------------


def test_the_generated_rule_passes_udevadm_verify(fake_rmos, tmp_path):
    if not shutil.which("udevadm"):
        pytest.skip("udevadm not installed")
    path = tmp_path / "99-rmos.rules"
    path.write_text(rules_of(dry_run(fake_rmos).stdout), encoding="utf-8")

    result = subprocess.run(["udevadm", "verify", str(path)], text=True, capture_output=True)

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_generated_unit_passes_systemd_analyze(fake_rmos, tmp_path):
    if not shutil.which("systemd-analyze"):
        pytest.skip("systemd-analyze not installed")
    path = tmp_path / "rmos-sync.service"
    path.write_text(unit_of(dry_run(fake_rmos, "--notify").stdout), encoding="utf-8")

    result = subprocess.run(
        ["systemd-analyze", "verify", "--user", str(path)], text=True, capture_output=True
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_shellcheck_is_clean_when_available():
    if not shutil.which("shellcheck"):
        pytest.skip("shellcheck not installed")
    result = subprocess.run(
        ["shellcheck", "-s", "sh", str(INSTALL), str(USB / "uninstall-usb-sync.sh")],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout
