"""Build the desktop app.

    python -m pip install -e ".[app,build]"
    python packaging/build.py

macOS: dist/Tally.app and dist/Tally.dmg
Windows: dist/Tally.exe and dist/Tally-windows.zip

Build on the system you're building for; PyInstaller can't cross-compile.
"""

import plistlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))

from tally import __version__  # noqa: E402


def pyinstaller(*args: str) -> None:
    sep = ";" if sys.platform.startswith("win") else ":"
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", "Tally", "--distpath", str(DIST), "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"), "--paths", str(ROOT),
        "--collect-submodules", "tally",
        "--add-data", f"{ROOT / 'tally' / 'web'}{sep}tally/web",
        "--add-data", f"{ROOT / 'tally' / 'assets'}{sep}tally/assets",
        *args, str(ROOT / "packaging" / "tally_app.py"),
    ], check=True, cwd=ROOT)


def build_mac() -> None:
    # Apple Silicon only; Pillow is only needed for the Windows tray
    pyinstaller("--icon", str(ROOT / "packaging" / "Tally.icns"), "--osx-bundle-identifier", "com.tally.app",
                "--target-arch", "arm64", "--exclude-module", "PIL")
    app = DIST / "Tally.app"
    info_path = app / "Contents" / "Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    info.update({
        "CFBundleDisplayName": "Tally",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSApplicationCategoryType": "public.app-category.entertainment",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        # The always-on logger lives in the menu bar; the window process puts itself in the Dock
        "LSUIElement": True,
    })
    info_path.write_bytes(plistlib.dumps(info))

    # Editing Info.plist breaks the signature PyInstaller made, so sign again
    # (ad hoc). codesign refuses files with Finder metadata, which iCloud keeps
    # adding inside ~/Documents, so sign and package a clean copy in a temp folder.
    dmg = DIST / "Tally.dmg"
    dmg.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "Tally"
        stage.mkdir()
        signed = stage / "Tally.app"
        subprocess.run(["ditto", "--norsrc", "--noextattr", "--noqtn", str(app), str(signed)], check=True)
        subprocess.run(["xattr", "-cr", str(signed)], check=True)
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(signed)], check=True)
        build_dmg(signed, dmg)
        shutil.rmtree(app)
        subprocess.run(["ditto", "--norsrc", "--noextattr", str(signed), str(app)], check=True)
    print(f"\nBuilt {app}\n      {dmg}")


def build_dmg(app: Path, dmg: Path) -> None:
    """The installer: a window with Tally on the left and Applications on the right."""
    import dmgbuild

    background = ROOT / "packaging" / "dmg-background.tiff"
    if not background.exists():
        subprocess.run([sys.executable, str(ROOT / "packaging" / "make_dmg_background.py")], check=True)
    dmgbuild.build_dmg(str(dmg), "Tally", settings={
        "files": [str(app)],
        "symlinks": {"Applications": "/Applications"},
        "icon": str(ROOT / "packaging" / "Tally.icns"),
        "background": str(background),
        "window_rect": ((200, 140), (640, 400)),  # matches make_dmg_background.py
        "icon_size": 112,
        "text_size": 13,
        "icon_locations": {"Tally.app": (170, 190), "Applications": (470, 190)},
        "show_status_bar": False, "show_tab_view": False, "show_toolbar": False,
        "show_pathbar": False, "show_sidebar": False,
        "format": "UDZO",
    })


def build_windows() -> None:
    pyinstaller("--onefile", "--icon", str(ROOT / "packaging" / "Tally.ico"),
                "--hidden-import", "pystray._win32")
    exe = DIST / "Tally.exe"
    with zipfile.ZipFile(DIST / "Tally-windows.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(exe, "Tally.exe")
    print(f"\nBuilt {exe}\n      {DIST / 'Tally-windows.zip'}")


def main() -> None:
    if not (ROOT / "tally" / "assets" / "icon.png").exists():
        subprocess.run([sys.executable, str(ROOT / "packaging" / "make_icons.py")], check=True)
    shutil.rmtree(DIST / "Tally.app", ignore_errors=True)
    if sys.platform == "darwin":
        build_mac()
    elif sys.platform.startswith("win"):
        build_windows()
    else:
        sys.exit("Build Tally on macOS or Windows.")


if __name__ == "__main__":
    main()
