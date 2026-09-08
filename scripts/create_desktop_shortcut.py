#!/usr/bin/env python3
"""Create no-install, no-admin, double-click launchers for the STL Rebuilder GUI.

Run standalone:

    .venv/bin/python scripts/create_desktop_shortcut.py [--desktop-dir DIR] [--repo DIR]

or import it (this is what ``Help -> Create Desktop Shortcut...`` does)::

    from scripts.create_desktop_shortcut import create_shortcuts
    created = create_shortcuts(repo_root)          # -> list[str] of paths written

Everything generated here is a plain text file plus (on macOS) a hand-built ``.app`` bundle and
an ``.icns`` drawn from scratch with Pillow.  No installer, no admin rights, no code signing, no
network access at any point.

Absolute paths are baked in at generation time.  That is deliberate and cheap: if the repository
moves, re-run this script (or the menu item) and the launchers are correct again.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import stat
import subprocess
import sys
from pathlib import Path

APP_NAME = "STL Rebuilder"
BUNDLE_ID = "local.stl-rebuilder.launcher"
ICON_BASENAME = "AppIcon"  # CFBundleIconFile; the file is <ICON_BASENAME>.icns

# The Windows .lnk is created by PowerShell rather than pywin32 (no third-party dependency, and
# PowerShell is on every corporate Windows image).  It lives in ONE constant so that a fix from
# the work machine -- the only place this path can actually be exercised -- is a one-line edit,
# and so the test suite can assert on it here without running PowerShell.
#
# GetFolderPath('Desktop') is load-bearing: corporate machines routinely redirect the Desktop
# into OneDrive, where %USERPROFILE%\Desktop is simply the wrong directory.
# pythonw.exe (not python.exe) is what keeps the console window from appearing.
WINDOWS_LNK_PS_TEMPLATE = (
    "$d=[Environment]::GetFolderPath('Desktop'); "
    "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(\"$d\\{app_name}.lnk\"); "
    "$s.TargetPath='{repo}\\.venv\\Scripts\\pythonw.exe'; "
    "$s.Arguments='-m app'; "
    "$s.WorkingDirectory='{repo}'; "
    "$s.IconLocation='{repo}\\.venv\\Scripts\\pythonw.exe'; "
    "$s.Description='{app_name} -- burnback STL to STEP solid'; "
    "$s.Save()"
)

# --- icon palette -----------------------------------------------------------------------------
# Deliberately the application's own tokens, not a fresh set: BG_* / VIEWPORT_BG_* from
# app/theme.py, and the rebuilt-solid blue from app/viewport.py's SOLID_MESH_COLOR, so the Dock
# icon and the thing on screen are recognisably the same product.
ICON_BG_TOP = (0x20, 0x25, 0x2C)      # theme.VIEWPORT_BG_TOP
ICON_BG_BOTTOM = (0x0D, 0x0F, 0x12)   # theme.VIEWPORT_BG_BOTTOM
ICON_GRAIN = (0x3F, 0x9F, 0xDC)       # viewport.SOLID_MESH_COLOR -- the rebuilt solid
ICON_AXIS = (0x35, 0xB8, 0xC8)        # theme.TELEMETRY -- "live data" cyan

# (base size, "@2x"?) -> the ten files `iconutil` expects inside a .iconset directory.
ICONSET_SIZES = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]


class ShortcutError(RuntimeError):
    """Anything that stops a launcher being written, phrased for a non-developer."""


# ------------------------------------------------------------------------------------------------
# paths
# ------------------------------------------------------------------------------------------------

def default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def venv_python(repo: Path, platform: str = None) -> Path:
    """The interpreter a launcher must call.  Never falls back to system python: a launcher that
    silently runs the wrong interpreter fails much later and much more confusingly than one that
    refuses to be created."""
    platform = platform or sys.platform
    if platform == "win32":
        path = repo / ".venv" / "Scripts" / "pythonw.exe"
    else:
        path = repo / ".venv" / "bin" / "python"
    if not path.exists():
        raise ShortcutError(
            f"no virtual environment found at {path}\n"
            "Create the venv first -- see WORK_SETUP.md section 3."
        )
    return path


def _rm(path: Path):
    """Delete before every write.  Two reasons, both real: on APFS an in-place overwrite reuses
    the inode and keeps the ORIGINAL 'Date Created', so a freshly regenerated launcher shows a
    stale date in Finder; and a stale .app bundle would otherwise keep files this run no longer
    generates."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _write_executable(path: Path, text: str):
    _rm(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ------------------------------------------------------------------------------------------------
# icon
# ------------------------------------------------------------------------------------------------

def _draw_icon(size: int):
    """One icon bitmap: a motor-grain cross-section on the viewport's own dark ground.

    The subject is literally what this application reconstructs -- an axisymmetric grain seen in
    the meridian plane: a domed-barrel-domed outer silhouette with the bore knocked out down the
    centre.  Flat two-tone, no gloss, no perspective, because the governing constraint is the
    16x16 Finder/Dock size, where anything with interior detail turns to mush.

    Drawn at 4x and downsampled (Lanczos) -- Pillow's shape primitives are not antialiased.
    The dashed axis line is the one size-adaptive element: below 128px it costs more legibility
    than it adds, so it is simply not drawn, the same way real icon families simplify at small
    sizes.
    """
    from PIL import Image, ImageDraw

    ss = 4
    n = size * ss
    grain_layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))

    # 1. vertical gradient ground, clipped to a rounded square. Built one pixel wide and then
    #    stretched: the 512@2x tile is 4096x4096 supersampled, and a per-pixel Python loop over
    #    16.7M pixels is the difference between a sub-second menu action and a ten-second one.
    column = Image.new("RGB", (1, n))
    cpx = column.load()
    for y in range(n):
        t = y / max(n - 1, 1)
        cpx[0, y] = tuple(round(a + (b - a) * t) for a, b in zip(ICON_BG_TOP, ICON_BG_BOTTOM))
    bg = column.resize((n, n), Image.NEAREST).convert("RGBA")

    margin = round(n * 0.055)
    radius = round(n * 0.215)
    plate = Image.new("L", (n, n), 0)
    ImageDraw.Draw(plate).rounded_rectangle(
        [margin, margin, n - 1 - margin, n - 1 - margin], radius=radius, fill=255)
    bg.putalpha(plate)

    # 2. the dashed axis of revolution, behind the grain, only where it is legible
    if size >= 128:
        d = ImageDraw.Draw(bg)
        line_w = max(round(n * 0.012), 1)
        dash, gap = round(n * 0.055), round(n * 0.035)
        # Runs visibly past both dome tips (the grain spans 0.13n..0.87n): a line that stopped
        # just short of them read as two stray ticks rather than an axis of revolution.
        y = margin + round(n * 0.02)
        end = n - 1 - margin - round(n * 0.02)
        cx = n // 2
        while y < end:
            d.line([(cx, y), (cx, min(y + dash, end))], fill=ICON_AXIS + (150,), width=line_w)
            y += dash + gap

    # 3. grain silhouette: outer profile (dome / barrel / dome) minus the bore, as ONE mask, so
    #    the bore is a true knockout showing the ground and the axis line through it.
    #    The domes are ELLIPTICAL, not semicircular, and that is the whole design: a stadium
    #    shape with circular ends reads as the digit 0 (verified by rendering it), whereas
    #    shallow domes over a straight-sided barrel read as a pressure vessel.
    def _profile(draw, w, h, dome, value):
        x0, x1 = (n - w) // 2, (n + w) // 2
        y0, y1 = (n - h) // 2, (n + h) // 2
        draw.ellipse([x0, y0, x1, y0 + 2 * dome], fill=value)          # forward dome
        draw.rectangle([x0, y0 + dome, x1, y1 - dome], fill=value)     # barrel
        draw.ellipse([x0, y1 - 2 * dome, x1, y1], fill=value)          # aft dome

    mask = Image.new("L", (n, n), 0)
    md = ImageDraw.Draw(mask)
    _profile(md, round(n * 0.42), round(n * 0.74), round(n * 0.115), 255)
    _profile(md, round(n * 0.15), round(n * 0.46), round(n * 0.045), 0)

    grain_layer.paste(Image.new("RGBA", (n, n), ICON_GRAIN + (255,)), (0, 0), mask)
    out = Image.alpha_composite(bg, grain_layer)
    return out.resize((size, size), Image.LANCZOS)


def build_icns(dest_dir: Path) -> Path | None:
    """Draw the .iconset and convert it with the built-in macOS `iconutil`.

    Returns the .icns path, or None when the icon cannot be produced (no Pillow, not macOS, no
    iconutil).  A missing icon degrades to the generic application icon; it never blocks the
    launcher itself, which is the part that actually matters.
    """
    try:
        import PIL  # noqa: F401
    except ImportError:
        return None
    if sys.platform != "darwin" or shutil.which("iconutil") is None:
        return None

    iconset = dest_dir / f"{ICON_BASENAME}.iconset"
    icns = dest_dir / f"{ICON_BASENAME}.icns"
    _rm(iconset)
    _rm(icns)
    iconset.mkdir(parents=True, exist_ok=True)

    rendered: dict[int, object] = {}
    for name, px in ICONSET_SIZES:
        if px not in rendered:
            rendered[px] = _draw_icon(px)
        rendered[px].save(iconset / name)

    proc = subprocess.run(
        ["iconutil", "-c", "icns", str(iconset)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not icns.exists():
        return None
    return icns


# ------------------------------------------------------------------------------------------------
# launchers
# ------------------------------------------------------------------------------------------------

def _unix_launch_script(repo: Path, python: Path) -> str:
    return (
        "#!/bin/zsh\n"
        f'cd "{repo}" || exit 1\n'
        f'exec "{python}" -m app\n'
    )


def create_macos_app(repo: Path, desktop_dir: Path, python: Path) -> Path:
    """A minimal hand-written .app bundle.

    Chosen over a bare .command as the primary launcher because double-clicking a .command opens
    a Terminal window that stays up for the whole session and lingers after quit -- exactly the
    "why is a terminal open" experience these launchers exist to remove.  The bundle is three
    files (plist, launch script, icon) and gives a real Dock presence with no stray window.
    """
    bundle = desktop_dir / f"{APP_NAME}.app"
    _rm(bundle)
    macos_dir = bundle / "Contents" / "MacOS"
    resources = bundle / "Contents" / "Resources"
    macos_dir.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    info = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleExecutable": "launch",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1.0",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
    }

    icns = build_icns(repo / "launchers")
    if icns is not None:
        shutil.copyfile(icns, resources / f"{ICON_BASENAME}.icns")
        info["CFBundleIconFile"] = ICON_BASENAME

    (bundle / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    _write_executable(macos_dir / "launch", _unix_launch_script(repo, python))
    # Nudge Finder's icon cache: it keys off the bundle's own mtime, and without this a
    # regenerated icon can keep showing the previous artwork for a surprisingly long time.
    os.utime(bundle, None)
    return bundle


def create_command_launcher(repo: Path, launchers_dir: Path, python: Path) -> Path:
    """The debugging path: same two lines, but a .command shows the console output that the .app
    swallows.  Kept alongside the bundle precisely for the "it bounced and quit, why?" case."""
    path = launchers_dir / "stl-rebuilder.command"
    _write_executable(path, _unix_launch_script(repo, python))
    return path


def create_windows_bat(repo: Path, launchers_dir: Path) -> Path:
    """Generated on every platform (it is just text) but only meaningful on Windows.  Uses
    python.exe, not pythonw.exe: this is the console-visible debug counterpart to the .lnk."""
    path = launchers_dir / "stl-rebuilder.bat"
    _rm(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "@echo off\r\n"
        f'cd /d "{_windows_repo(repo)}"\r\n'
        '".venv\\Scripts\\python.exe" -m app\r\n'
        "if errorlevel 1 pause\r\n",
        encoding="utf-8",
    )
    return path


def _windows_repo(repo: Path) -> str:
    """Windows-shaped spelling of the repo path.  On macOS this is only ever written into text
    that a Windows machine will later read, so it must not depend on os.sep."""
    return str(repo).replace("/", "\\")


def windows_lnk_command(repo: Path) -> str:
    return WINDOWS_LNK_PS_TEMPLATE.format(app_name=APP_NAME, repo=_windows_repo(repo))


def create_windows_lnk(repo: Path) -> Path | None:
    """Only ever executed on Windows; on any other platform the command string is still generated
    (and asserted on by the tests) but PowerShell is not invoked."""
    if sys.platform != "win32":
        return None
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", windows_lnk_command(repo)],
        check=True, capture_output=True,
    )
    desktop = Path(
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Environment]::GetFolderPath('Desktop')"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    )
    return desktop / f"{APP_NAME}.lnk"


# ------------------------------------------------------------------------------------------------
# entry points
# ------------------------------------------------------------------------------------------------

def create_shortcuts(repo_root=None, desktop_dir=None) -> list[str]:
    """Write every launcher appropriate to this machine.  Returns the paths created, in the order
    they should be shown to a user (the double-click one first)."""
    repo = Path(repo_root).resolve() if repo_root else default_repo_root()
    if not (repo / "app").is_dir():
        raise ShortcutError(f"{repo} does not look like the stl-rebuilder repository (no app/).")

    python = venv_python(repo)
    launchers = repo / "launchers"
    launchers.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    if sys.platform == "darwin":
        desktop = Path(desktop_dir).resolve() if desktop_dir else Path.home() / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        created.append(str(create_macos_app(repo, desktop, python)))
        created.append(str(create_command_launcher(repo, launchers, python)))
    elif sys.platform == "win32":
        lnk = create_windows_lnk(repo)
        if lnk is not None:
            created.append(str(lnk))
    else:
        created.append(str(create_command_launcher(repo, launchers, python)))

    created.append(str(create_windows_bat(repo, launchers)))
    return created


def completion_message(created: list[str]) -> str:
    """Shared by the CLI and the Help-menu dialog so the two never drift apart."""
    lines = ["Created:", ""]
    lines += [f"  {p}" for p in created]
    lines.append("")
    if sys.platform == "darwin":
        lines += [
            "First launch only: right-click the app on the Desktop, choose Open, then Open "
            "again. macOS blocks a plain double-click of any unsigned app the first time "
            '("cannot verify the developer"). After that, double-click works normally.',
            "",
            "If the app bounces and quits, run launchers/stl-rebuilder.command instead -- it "
            "shows the error the app bundle swallows.",
            "",
        ]
    lines.append("The repository path is baked in. If you move the repository, run this again.")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=None, help="repository root (default: this script's repo)")
    parser.add_argument("--desktop-dir", default=None,
                        help="where to write the double-click launcher (default: ~/Desktop)")
    args = parser.parse_args(argv)
    try:
        created = create_shortcuts(args.repo, args.desktop_dir)
    except ShortcutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(completion_message(created))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
