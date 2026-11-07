"""Printer profiles."""
import pytest

from agentcad.printers import PROFILES, get_printer, load_user_profiles


def test_builtin_profiles_are_generic_envelopes():
    assert PROFILES["generic-220"].bed_xyz == (220, 220, 250)
    assert PROFILES["generic-350"].bed_xyz == (350, 350, 340)
    assert set(PROFILES) == {"generic-180", "generic-220", "generic-256", "generic-300", "generic-350"}


def test_user_profiles_load_and_override(tmp_path, monkeypatch):
    file = tmp_path / "printers.toml"
    file.write_text('[printers.mine]\nbed = [100, 110, 120]\nnozzle_mm = 0.6\n')
    assert load_user_profiles(file)["mine"].bed_xyz == (100, 110, 120)
    monkeypatch.setattr("agentcad.printers.load_user_profiles", lambda path=None: load_user_profiles(file))
    assert get_printer("mine").nozzle_mm == 0.6


def test_user_profile_errors(tmp_path):
    file = tmp_path / "printers.toml"
    file.write_text('[printers.bad]\nbed = [1, 2]\n')
    with pytest.raises(ValueError, match="three positive"):
        load_user_profiles(file)
    file.write_text('[printers.bad]\nbedd = [1, 2, 3]\n')
    with pytest.raises(ValueError, match="bedd"):
        load_user_profiles(file)
    assert load_user_profiles(tmp_path / "missing.toml") == {}


def test_unknown_printer_lists_the_options():
    with pytest.raises(ValueError, match="generic-220"):
        get_printer("nope")
