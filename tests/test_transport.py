"""Tests for the SSH transport options that unattended runs depend on."""

import argparse
import subprocess

import pytest

from rmos import cli

HOST = "10.11.99.1"


def config(**overrides):
    return cli.Config(host=HOST, user="root", **overrides)


def fake_run(results):
    """Return a subprocess.run stand-in yielding the given return codes."""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        code = results[min(len(calls) - 1, len(results) - 1)]
        return subprocess.CompletedProcess(argv, code, "rmos-ok\n" if code == 0 else "", "")

    run.calls = calls
    return run


# --------------------------------------------------------------------------
# ssh argument construction
# --------------------------------------------------------------------------


def test_batch_mode_is_off_by_default():
    assert "BatchMode=yes" not in cli.Ssh(config()).args()


def test_batch_mode_is_requested_when_asked():
    """Unattended runs have no terminal, so ssh must fail rather than prompt."""
    args = cli.Ssh(config(), batch=True).args()
    assert args[args.index("BatchMode=yes") - 1] == "-o"


def test_multiplexing_is_on_by_default():
    assert any(a.startswith("ControlPath=") for a in cli.Ssh(config()).args())


def test_multiplexing_can_be_disabled():
    assert not any(a.startswith("ControlPath=") for a in cli.Ssh(config(multiplex=False)).args())


def test_configured_ssh_options_are_passed_through():
    args = cli.Ssh(config(ssh_options=["-i", "/keys/rm"])).args()
    assert args[args.index("-i") + 1] == "/keys/rm"
    assert args[-1] == "root@10.11.99.1"


def test_connect_timeout_is_applied():
    assert f"ConnectTimeout={7}" in cli.Ssh(config(connect_timeout=7)).args()


# --------------------------------------------------------------------------
# Waiting for the tablet
# --------------------------------------------------------------------------


def test_wait_returns_immediately_when_the_tablet_answers(monkeypatch):
    run = fake_run([0])
    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)

    cli.Ssh(config()).wait_for_device(30)

    assert len(run.calls) == 1


def test_wait_retries_until_the_tablet_is_ready(monkeypatch):
    """The USB interface appears before sshd accepts connections."""
    run = fake_run([255, 255, 0])
    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)

    cli.Ssh(config()).wait_for_device(30)

    assert len(run.calls) == 3


def test_wait_gives_up_rather_than_hanging(monkeypatch):
    run = fake_run([255])
    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)

    clock = iter([0.0, 0.0, 100.0, 100.0])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(clock))

    with pytest.raises(cli.RmosError, match="not reachable"):
        cli.Ssh(config()).wait_for_device(30)


def test_make_ssh_waits_only_when_asked(monkeypatch):
    waited = []
    monkeypatch.setattr(cli.Ssh, "wait_for_device", lambda self, s: waited.append(s))

    cli.make_ssh(config(), argparse.Namespace(verbose=False, batch=False, wait=0))
    assert waited == []

    cli.make_ssh(config(), argparse.Namespace(verbose=False, batch=True, wait=45))
    assert waited == [45]


def test_make_ssh_propagates_the_batch_flag():
    ssh = cli.make_ssh(config(), argparse.Namespace(verbose=False, batch=True, wait=0))
    assert ssh.batch is True
