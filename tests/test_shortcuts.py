"""`scripts/create_desktop_shortcut.py`: the double-click launchers are generated correctly.

Pure filesystem, no Qt. Every test runs against a FAKE repo tree in tmp_path and writes its
Desktop artifacts to tmp_path too -- nothing here may ever touch the real ~/Desktop.
"""
import os
import plistlib
import stat
import sys

import pytest

from scripts.create_desktop_shortcut import (
    APP_NAME, ICON_BASENAME, WINDOWS_LNK_PS_TEMPLATE, ShortcutError, build_icns,
    create_shortcuts, venv_python, windows_lnk_command,
)

macos_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS launcher")


@pytest.fixture
def fake_repo(tmp_path):
    """The minimum tree create_shortcuts() insists on: an app/ package and a venv interpreter."""
    repo = tmp_path / "stl-rebuilder"
    (repo / "app").mkdir(parents=True)
    if sys.platform == "win32":
        venv_bin = repo / ".venv" / "Scripts"
        venv_bin.mkdir(parents=True)
        (venv_bin / "pythonw.exe").write_text("")
    else:
        venv_bin = repo / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").write_text("")
    return repo


@pytest.fixture
def desktop(tmp_path):
    d = tmp_path / "Desktop"
    d.mkdir()
    return d


# ---- preconditions -----------------------------------------------------------------------------

def test_missing_venv_is_a_plain_message_not_a_fallback(tmp_path, desktop):
    """A launcher that silently ran the system python would fail much later and much more
    confusingly than one that refuses to be created."""
    repo = tmp_path / "no-venv"
    (repo / "app").mkdir(parents=True)
    with pytest.raises(ShortcutError) as exc:
        create_shortcuts(repo, desktop_dir=desktop)
    assert "WORK_SETUP.md" in str(exc.value)


def test_non_repo_directory_is_rejected(tmp_path, desktop):
    with pytest.raises(ShortcutError):
        create_shortcuts(tmp_path, desktop_dir=desktop)


def test_venv_python_points_into_the_repo(fake_repo):
    assert venv_python(fake_repo).is_relative_to(fake_repo / ".venv")


# ---- macOS .app bundle -------------------------------------------------------------------------

@macos_only
def test_app_bundle_structure_and_plist(fake_repo, desktop):
    created = create_shortcuts(fake_repo, desktop_dir=desktop)
    bundle = desktop / f"{APP_NAME}.app"
    assert str(bundle) in created
    assert bundle.is_dir()

    plist = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    assert plist["CFBundleExecutable"] == "launch"
    assert plist["CFBundleName"] == APP_NAME
    assert plist["CFBundlePackageType"] == "APPL"
    assert plist["CFBundleIdentifier"] == "local.stl-rebuilder.launcher"
    assert plist["LSMinimumSystemVersion"] == "11.0"


@macos_only
def test_bundle_launch_script_is_executable_and_baked_with_the_repo_path(fake_repo, desktop):
    create_shortcuts(fake_repo, desktop_dir=desktop)
    launch = desktop / f"{APP_NAME}.app" / "Contents" / "MacOS" / "launch"
    assert os.access(launch, os.X_OK)
    assert launch.stat().st_mode & stat.S_IXUSR
    text = launch.read_text()
    assert str(fake_repo) in text
    assert str(fake_repo / ".venv" / "bin" / "python") in text
    assert "-m app" in text
    assert text.startswith("#!/bin/zsh")


@macos_only
def test_bundle_carries_a_real_icon(fake_repo, desktop):
    pytest.importorskip("PIL")
    create_shortcuts(fake_repo, desktop_dir=desktop)
    plist = plistlib.loads(
        (desktop / f"{APP_NAME}.app" / "Contents" / "Info.plist").read_bytes())
    assert plist["CFBundleIconFile"] == ICON_BASENAME
    icns = desktop / f"{APP_NAME}.app" / "Contents" / "Resources" / f"{ICON_BASENAME}.icns"
    assert icns.is_file()
    assert icns.stat().st_size > 1024, "an .icns this small is an empty or failed conversion"


@macos_only
def test_build_icns_renders_the_full_iconset(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image
    icns = build_icns(tmp_path)
    assert icns is not None and icns.stat().st_size > 1024
    iconset = tmp_path / f"{ICON_BASENAME}.iconset"
    # The ten filenames `iconutil` expects; a missing one is silently dropped by iconutil, so
    # assert on the set rather than trusting the conversion's exit code.
    for name, px in (("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
                     ("icon_512x512@2x.png", 1024)):
        png = iconset / name
        assert png.is_file(), name
        with Image.open(png) as im:
            assert im.size == (px, px)
            # Not a blank plate: the grain colour must actually be present.
            assert len(im.convert("RGB").getcolors(maxcolors=65536)) > 3, name
    assert len(list(iconset.glob("*.png"))) == 10


@macos_only
def test_command_fallback_is_executable(fake_repo, desktop):
    create_shortcuts(fake_repo, desktop_dir=desktop)
    cmd = fake_repo / "launchers" / "stl-rebuilder.command"
    assert cmd.is_file()
    assert os.access(cmd, os.X_OK)
    text = cmd.read_text()
    assert "-m app" in text
    assert str(fake_repo) in text


# ---- Windows artifacts (generated everywhere, executed only on Windows) -------------------------

def test_bat_is_the_console_visible_debug_path(fake_repo, desktop):
    create_shortcuts(fake_repo, desktop_dir=desktop)
    bat = fake_repo / "launchers" / "stl-rebuilder.bat"
    assert bat.is_file()
    text = bat.read_text()
    assert "python.exe" in text and "pythonw.exe" not in text
    assert "-m app" in text
    assert "@echo off" in text
    assert "pause" in text
    assert str(fake_repo).replace("/", "\\") in text


def test_lnk_powershell_template_targets_pythonw(fake_repo):
    """The .lnk cannot be created on this machine, so the command string itself is the contract."""
    assert "pythonw.exe" in WINDOWS_LNK_PS_TEMPLATE
    cmd = windows_lnk_command(fake_repo)
    assert "pythonw.exe" in cmd
    assert "-m app" in cmd
    # Desktop redirection into OneDrive is common on corporate machines; %USERPROFILE%\Desktop
    # would be the wrong folder there.
    assert "GetFolderPath('Desktop')" in cmd
    assert "%USERPROFILE%" not in cmd
    assert f"{APP_NAME}.lnk" in cmd
    assert str(fake_repo).replace("/", "\\") in cmd


# ---- rerunning -----------------------------------------------------------------------------

def test_rerun_is_idempotent(fake_repo, desktop):
    """Delete-before-write: a second run must not fail on the leftovers of the first, and must
    produce the same set of paths."""
    first = create_shortcuts(fake_repo, desktop_dir=desktop)
    second = create_shortcuts(fake_repo, desktop_dir=desktop)
    assert first == second
    for path in second:
        assert os.path.exists(path)


@macos_only
def test_rerun_clears_stale_files_out_of_the_bundle(fake_repo, desktop):
    create_shortcuts(fake_repo, desktop_dir=desktop)
    stale = desktop / f"{APP_NAME}.app" / "Contents" / "MacOS" / "leftover-from-an-old-build"
    stale.write_text("x")
    create_shortcuts(fake_repo, desktop_dir=desktop)
    assert not stale.exists()
