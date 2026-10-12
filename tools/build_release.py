"""Build the one-download release package: dist/VTTA-<version>.zip

The ZIP contains a single "VTTA" folder with the app, its icon and the double-click
launchers. Users unzip it anywhere and open "Start VTTA" - the first run sets up
everything else inside that folder.

Usage:  python tools/build_release.py
"""
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    "Voice_To_Text_Assistant.py",
    "requirements.txt",
    "Start VTTA.bat",
    "Start VTTA.command",
    "start-vtta.sh",
    "assets/vtta.png",
    "assets/vtta.ico",
    "README.md",
    "INSTALL-Windows.md",
    "SECURITY.md",
    "LICENSE",
]
EXECUTABLE = {"Start VTTA.command", "start-vtta.sh"}
START_HERE = """VTTA - Voice to Text Assistant
==============================

1. Keep this whole folder together. You can put it anywhere (and move it later).

2. Start VTTA:
     Windows : double-click  "Start VTTA.bat"
     macOS   : double-click  "Start VTTA.command"
               (the first time: right-click it and choose Open)
     Linux   : run  ./start-vtta.sh

3. The first start sets everything up inside this folder (needs internet,
   about 1 GB, a few minutes). After that VTTA starts right away.

Using VTTA: press Ctrl+Shift+R, speak, press it again - the text is pasted
where your cursor is. More in README.md.

macOS: allow the Terminal app under System Settings > Privacy & Security >
Microphone, Accessibility and Input Monitoring, then start VTTA again.

Licensed for noncommercial use only - see LICENSE.
"""


def version():
    src = (ROOT / "Voice_To_Text_Assistant.py").read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION = "([^"]+)"', src, re.M)
    if not m:
        sys.exit("APP_VERSION not found")
    return m.group(1)


def add(zf, arcname, data, executable=False):
    info = zipfile.ZipInfo(arcname, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # unix, so the permission bits below are honoured
    info.external_attr = (0o100755 if executable else 0o100644) << 16
    zf.writestr(info, data)


def main():
    missing = [f for f in FILES if not (ROOT / f).is_file()]
    if missing:
        sys.exit(f"Missing files: {missing}")
    out = ROOT / "dist" / f"VTTA-{version()}.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w") as zf:
        for f in FILES:
            data = (ROOT / f).read_bytes()
            if f.endswith(".bat"):
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")  # cmd.exe needs CRLF
            elif f.endswith((".sh", ".command")):
                data = data.replace(b"\r\n", b"\n")
            add(zf, f"VTTA/{f}", data, executable=f in EXECUTABLE)
        add(zf, "VTTA/START HERE.txt", START_HERE.replace("\n", "\r\n").encode("utf-8"))
    print(f"Built {out.relative_to(ROOT)} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
