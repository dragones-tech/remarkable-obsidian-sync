"""Tests for configuration loading, layering, and the file rmos writes itself."""

import pytest

from rmos.settings import (
    Config,
    ConfigError,
    config_from_tables,
    dump_toml,
    load_config,
    local_path_for,
    read_setting,
    unset_setting,
    write_setting,
)


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[obsidian]\nvault = "/vault"\n', encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Layering
# --------------------------------------------------------------------------


def test_the_local_file_overrides_the_hand_written_one(config_path):
    config_path.write_text('[obsidian]\nvault = "/vault"\n\n[remarkable]\nhost = "10.11.99.1"\n', encoding="utf-8")
    local_path_for(config_path).write_text('[remarkable]\nhost = "192.168.1.5"\n', encoding="utf-8")

    assert load_config(config_path).host == "192.168.1.5"


def test_settings_the_local_file_does_not_mention_are_kept(config_path):
    config_path.write_text('[obsidian]\nvault = "/vault"\nsource = "Notes/rM"\n', encoding="utf-8")
    local_path_for(config_path).write_text('[obsidian]\nvault = "/other"\n', encoding="utf-8")

    cfg = load_config(config_path)
    assert cfg.vault.as_posix() == "/other"
    assert cfg.source == "Notes/rM", "an override of one key must not drop its siblings"


def test_a_local_file_alone_is_enough(tmp_path):
    path = tmp_path / "config.toml"
    local_path_for(path).write_text('[obsidian]\nvault = "/vault"\n', encoding="utf-8")
    assert load_config(path).vault.as_posix() == "/vault"


def test_a_missing_config_is_reported_with_a_next_step(tmp_path):
    with pytest.raises(ConfigError, match="init-config"):
        load_config(tmp_path / "absent.toml")


def test_malformed_toml_names_the_file(config_path):
    config_path.write_text("[obsidian\nvault =", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"config\.toml"):
        load_config(config_path)


def test_a_vault_is_required(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[remarkable]\nhost = "x"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="vault is required"):
        load_config(path)


# --------------------------------------------------------------------------
# Selection tags: plural, with the old singular still accepted
# --------------------------------------------------------------------------


def test_tags_may_be_a_list():
    cfg = config_from_tables({"obsidian": {"vault": "/v"}, "selection": {"tags": ["a", "b"]}})
    assert cfg.selection_tags == ("a", "b")


def test_the_older_singular_tag_still_works():
    cfg = config_from_tables({"obsidian": {"vault": "/v"}, "selection": {"tag": "obsidian"}})
    assert cfg.selection_tags == ("obsidian",)


def test_tags_wins_over_tag_when_both_are_present():
    cfg = config_from_tables({"obsidian": {"vault": "/v"}, "selection": {"tag": "old", "tags": ["new"]}})
    assert cfg.selection_tags == ("new",)


def test_a_bare_string_is_accepted_as_a_one_item_list():
    cfg = config_from_tables({"obsidian": {"vault": "/v"}, "selection": {"tags": "solo"}})
    assert cfg.selection_tags == ("solo",)


def test_blank_tags_are_dropped():
    cfg = config_from_tables({"obsidian": {"vault": "/v"}, "selection": {"tags": ["a", "  ", ""]}})
    assert cfg.selection_tags == ("a",)


def test_tags_must_be_strings():
    with pytest.raises(ConfigError, match="list of strings"):
        config_from_tables({"obsidian": {"vault": "/v"}, "selection": {"tags": [1, 2]}})


def test_the_default_tag_is_obsidian():
    assert config_from_tables({"obsidian": {"vault": "/v"}}).selection_tags == ("obsidian",)


def test_selection_tag_names_the_first_of_several():
    assert Config(selection_tags=("a", "b")).selection_tag == "a"
    assert Config(selection_tags=()).selection_tag == ""


# --------------------------------------------------------------------------
# Reading and writing individual settings
# --------------------------------------------------------------------------


def test_set_writes_the_local_file_and_leaves_yours_alone(config_path):
    original = config_path.read_text(encoding="utf-8")

    written = write_setting(config_path, "selection.tags", ["sync", "obsidian"])

    assert written == local_path_for(config_path)
    assert config_path.read_text(encoding="utf-8") == original, "comments in config.toml must survive"
    assert load_config(config_path).selection_tags == ("sync", "obsidian")


def test_set_then_get_round_trips(config_path):
    write_setting(config_path, "remarkable.host", "10.11.99.2")
    result = read_setting(config_path, "remarkable.host")
    assert result["value"] == "10.11.99.2"
    assert result["source"] == str(local_path_for(config_path))


def test_get_falls_back_to_the_hand_written_file(config_path):
    assert read_setting(config_path, "obsidian.vault")["value"] == "/vault"
    assert read_setting(config_path, "obsidian.vault")["source"] == str(config_path)


def test_get_reports_an_unset_key_as_null(config_path):
    assert read_setting(config_path, "remarkable.host") == {
        "key": "remarkable.host",
        "value": None,
        "source": None,
    }


def test_setting_several_keys_accumulates(config_path):
    write_setting(config_path, "selection.tags", ["a"])
    write_setting(config_path, "remarkable.host", "1.2.3.4")
    cfg = load_config(config_path)
    assert cfg.selection_tags == ("a",)
    assert cfg.host == "1.2.3.4"


def test_unset_removes_the_override_and_restores_the_underlying_value(config_path):
    config_path.write_text('[obsidian]\nvault = "/vault"\nsource = "Mine"\n', encoding="utf-8")
    write_setting(config_path, "obsidian.source", "Theirs")
    assert load_config(config_path).source == "Theirs"

    unset_setting(config_path, "obsidian.source")

    assert load_config(config_path).source == "Mine"


def test_unsetting_something_that_was_never_set_is_harmless(config_path):
    unset_setting(config_path, "remarkable.host")
    assert load_config(config_path).host == "10.11.99.1"


# --------------------------------------------------------------------------
# What may be written
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["render.command", "remarkable.ssh_options", "render.backend", "rmos.state"])
def test_keys_that_could_launch_a_process_are_not_settable(config_path, key):
    """A UI bug must not be able to turn into command execution."""
    with pytest.raises(ConfigError, match="not settable"):
        write_setting(config_path, key, ["anything"])


def test_an_unknown_key_is_refused(config_path):
    with pytest.raises(ConfigError, match="not settable"):
        write_setting(config_path, "selection.nonsense", "x")


@pytest.mark.parametrize("key", ["selection", "a.b.c", "", "."])
def test_malformed_key_names_are_refused(config_path, key):
    with pytest.raises(ConfigError, match=r"table\.name"):
        write_setting(config_path, key, "x")


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("remarkable.multiplex", "yes", "true or false"),
        ("remarkable.connect_timeout", "ten", "whole number"),
        ("remarkable.connect_timeout", True, "whole number"),
        ("remarkable.host", 10, "a string"),
        ("selection.tags", "not-a-list", "list of strings"),
        ("selection.tags", [1], "list of strings"),
    ],
)
def test_values_of_the_wrong_type_are_refused(config_path, key, value, message):
    with pytest.raises(ConfigError, match=message):
        write_setting(config_path, key, value)


# --------------------------------------------------------------------------
# The TOML writer
# --------------------------------------------------------------------------


def test_written_toml_reads_back_identically(config_path):
    values = {
        "selection": {"tags": ["a", "b"], "sources": ["file", "tag"]},
        "remarkable": {"host": "10.11.99.1", "multiplex": False, "connect_timeout": 30},
        "obsidian": {"vault": "/home/me/Vault"},
    }
    for table, entries in values.items():
        for name, value in entries.items():
            write_setting(config_path, f"{table}.{name}", value)

    import tomllib

    with local_path_for(config_path).open("rb") as f:
        assert tomllib.load(f) == values


def test_quotes_and_backslashes_survive_a_round_trip(config_path):
    write_setting(config_path, "obsidian.vault", '/home/me/He said "hi"\\there')
    assert load_config(config_path).vault.as_posix() == '/home/me/He said "hi"\\there'


def test_the_written_file_says_not_to_edit_it():
    assert "config set" in dump_toml({"a": {"b": "c"}})


def test_empty_tables_are_not_written():
    assert "[a]" not in dump_toml({"a": {}})


def test_a_type_we_cannot_write_is_an_error_not_a_guess():
    with pytest.raises(ConfigError, match="Cannot write"):
        dump_toml({"a": {"b": 1.5}})
    with pytest.raises(ConfigError, match="Cannot write"):
        dump_toml({"a": {"b": {"nested": "too deep"}}})
