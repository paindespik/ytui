"""Firefox-fork profile resolution for `ytui auth cookies --from-browser`."""

import pytest

from ytui.auth import AuthError, _fork_profile

# Shape of a real Zen profiles.ini: the starter profile carries Default=1 while
# the profile the browser actually uses is named by the [Install…] section.
PROFILES_INI = """\
[Profile1]
Name=default
IsRelative=1
Path=f7307nyu.default
Default=1

[Profile0]
Name=Default (release)
IsRelative=1
Path=qp5y6dpr.Default (release)

[General]
StartWithLastProfile=1
Version=2

[Install15B76BAA26BA15E7]
Default=qp5y6dpr.Default (release)
Locked=1
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / "profiles.ini").write_text(PROFILES_INI, encoding="utf-8")
    return tmp_path


def test_install_default_wins_over_starter_profile(root):
    """The Default=1 profile is a decoy: it holds no session."""
    assert _fork_profile(root, None) == str(root / "qp5y6dpr.Default (release)")


def test_profile_selected_by_display_name(root):
    assert _fork_profile(root, "default") == str(root / "f7307nyu.default")


def test_profile_selected_by_directory_name(root):
    assert _fork_profile(root, "f7307nyu.default") == str(root / "f7307nyu.default")


def test_unknown_profile_lists_the_available_ones(root):
    with pytest.raises(AuthError, match="Default \\(release\\)"):
        _fork_profile(root, "nope")


def test_falls_back_to_default_flag_without_install_section(tmp_path):
    (tmp_path / "profiles.ini").write_text(
        "[Profile0]\nName=solo\nPath=abc.solo\nDefault=1\n", encoding="utf-8"
    )
    assert _fork_profile(tmp_path, None) == str(tmp_path / "abc.solo")


def test_missing_profiles_ini_is_a_readable_error(tmp_path):
    with pytest.raises(AuthError, match="No profiles.ini"):
        _fork_profile(tmp_path / "absent", None)


def test_no_default_anywhere_asks_for_an_explicit_profile(tmp_path):
    (tmp_path / "profiles.ini").write_text(
        "[Profile0]\nName=solo\nPath=abc.solo\n", encoding="utf-8"
    )
    with pytest.raises(AuthError, match="No default profile"):
        _fork_profile(tmp_path, None)
