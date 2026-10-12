"""Build the one-download release packages, one per platform:

    dist/VTTA-Windows.zip   dist/VTTA-macOS.zip   dist/VTTA-Linux.zip   dist/SHA256SUMS.txt

Each ZIP unzips to a tidy folder with only the launcher for that platform on top:

    VTTA/
      Start VTTA (Windows).bat      (or "Start VTTA (Mac).command" / "Start VTTA (Linux).sh")
      Read Me First.txt
      app/                          the program, icon, docs, "Troubleshoot VTTA (...)";
                                    its settings and private Python live here too

The file names stay the same for every version, so README links to
.../releases/latest/download/VTTA-Windows.zip always fetch the newest one, and the
in-app updater finds the right package for the platform it runs on.

Usage:  python tools/build_release.py
"""
import hashlib
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_FILES = [
    "Voice_To_Text_Assistant.py",
    "requirements.txt",
    "assets/vtta.png",
    "assets/vtta.ico",
    "README.md",
    "SECURITY.md",
    "LICENSE",
]
PLATFORMS = {
    # name: (top-level launcher, files from tools/ placed in app/, extra docs in app/)
    "Windows": ("Start VTTA (Windows).bat", ["Troubleshoot VTTA (Windows).bat"], ["INSTALL-Windows.md"]),
    "macOS": ("Start VTTA (Mac).command", ["launcher.sh", "Troubleshoot VTTA (Mac).command"], []),
    "Linux": ("Start VTTA (Linux).sh", ["launcher.sh", "Troubleshoot VTTA (Linux).sh"], []),
}

READ_ME = {
    "Windows": """VTTA - Voice to Text Assistant {version}  (Windows)
=================================================

START
  Double-click  "Start VTTA (Windows).bat"

  The first start sets everything up inside this VTTA folder: it needs internet,
  about 1 GB and a few minutes. If Python is missing it offers to install it, and
  at the end it can put a VTTA shortcut on your desktop. After that, VTTA opens
  right away. If Windows SmartScreen warns: More info > Run anyway.

USE
  Press Ctrl+Shift+R, speak, press Ctrl+Shift+R again - the text is pasted where
  your cursor is. Ctrl+Shift+S does the same for a web search.

UPDATES
  VTTA checks once a day and shows "Update to x.y" in its status bar. One click
  installs it and keeps your settings. (Help > Check for Updates; can be turned
  off in Settings > General.)

OPTIONS & TROUBLESHOOTING
  app\\Troubleshoot VTTA (Windows).bat  (also: Help > Troubleshooting & Options)
    - start with messages shown, if VTTA won't open
    - repair VTTA, create a desktop shortcut, free disk space

MOVE OR REMOVE
  Keep this folder together; you can move it anytime. To uninstall, turn off
  "Launch at sign-in" in Settings if you used it, then delete this folder.
""",
    "macOS": """VTTA - Voice to Text Assistant {version}  (Mac)
=============================================

START
  Double-click  "Start VTTA (Mac).command"
  The very first time macOS blocks downloaded scripts: right-click the file,
  choose Open, then Open again.

  The first start sets everything up inside this VTTA folder: it needs internet,
  about 1 GB and a few minutes (and Homebrew, https://brew.sh, for the audio
  library). After that, VTTA opens right away.

  Then allow "Terminal" in System Settings > Privacy & Security under
  Microphone, Accessibility and Input Monitoring, and start VTTA again.

USE
  Press Ctrl+Shift+R, speak, press Ctrl+Shift+R again - the text is pasted where
  your cursor is. Ctrl+Shift+S does the same for a web search.

UPDATES
  VTTA checks once a day and shows "Update to x.y" in its status bar. One click
  installs it and keeps your settings. (Help > Check for Updates.)

OPTIONS & TROUBLESHOOTING
  app/Troubleshoot VTTA (Mac).command  (also: Help > Troubleshooting & Options)
    - start with messages shown, if VTTA won't open
    - repair VTTA, free disk space

MOVE OR REMOVE
  Keep this folder together; you can move it anytime. To uninstall, turn off
  "Launch at login" in Settings if you used it, then delete this folder.
""",
    "Linux": """VTTA - Voice to Text Assistant {version}  (Linux)
===============================================

START
  ./"Start VTTA (Linux).sh"
  The first start sets everything up inside this folder (needs internet, ~1 GB).
  You may need: sudo apt install python3-venv python3-tk python3-dev portaudio19-dev xclip

USE
  Ctrl+Shift+R to dictate and paste, Ctrl+Shift+S for a web search
  (global hotkeys need root on Linux; the window's buttons always work).

UPDATES
  VTTA checks once a day and offers "Update to x.y" in its status bar.

OPTIONS & TROUBLESHOOTING
  app/Troubleshoot VTTA (Linux).sh - start with messages, repair, free disk space.

To uninstall, delete this folder.
""",
}


def version():
    src = (ROOT / "Voice_To_Text_Assistant.py").read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION = "([^"]+)"', src, re.M)
    if not m:
        sys.exit("APP_VERSION not found")
    return m.group(1)


def is_exec(name):
    return name.endswith((".sh", ".command"))


def normalize(name, data):
    if name.endswith(".bat"):
        return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")  # cmd.exe needs CRLF
    if name.endswith((".sh", ".command")):
        return data.replace(b"\r\n", b"\n")
    return data


def add(zf, arcname, data, executable=False):
    info = zipfile.ZipInfo(arcname, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # unix, so the permission bits below are honoured
    info.external_attr = (0o100755 if executable else 0o100644) << 16
    zf.writestr(info, data)


def build(platform, ver, out_dir):
    launcher, extras, docs = PLATFORMS[platform]
    files = APP_FILES + [f"tools/{e}" for e in extras] + docs + [launcher]
    missing = [f for f in files if not (ROOT / f).is_file()]
    if missing:
        sys.exit(f"Missing files: {missing}")
    out = out_dir / f"VTTA-{platform}.zip"
    with zipfile.ZipFile(out, "w") as zf:
        add(zf, f"VTTA/{launcher}", normalize(launcher, (ROOT / launcher).read_bytes()), is_exec(launcher))
        text = READ_ME[platform].format(version=ver)
        if platform == "Windows":
            text = text.replace("\n", "\r\n")
        add(zf, "VTTA/Read Me First.txt", text.encode("utf-8"))
        for f in APP_FILES + docs:
            add(zf, f"VTTA/app/{f}", normalize(f, (ROOT / f).read_bytes()))
        for e in extras:
            add(zf, f"VTTA/app/{e}", normalize(e, (ROOT / "tools" / e).read_bytes()), is_exec(e))
    return out


def main():
    ver = version()
    out_dir = ROOT / "dist"
    out_dir.mkdir(exist_ok=True)
    sums = []
    for platform in PLATFORMS:
        out = build(platform, ver, out_dir)
        sums.append(f"{hashlib.sha256(out.read_bytes()).hexdigest()}  {out.name}")
        print(f"Built dist/{out.name} ({out.stat().st_size / 1024:.0f} KB)")
    (out_dir / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(f"Version {ver}; checksums in dist/SHA256SUMS.txt")


if __name__ == "__main__":
    main()
