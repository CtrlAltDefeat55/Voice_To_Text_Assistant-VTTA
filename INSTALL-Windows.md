# Install & Run — Windows (Voice To Text Assistant)

These steps set up **VTTA** on **Windows 10/11**.

> VTTA records short dictations, transcribes them locally with Whisper, and can paste the text where your cursor is. It can also dictate a web search.

## Easiest way
1. Download [**VTTA-Windows.zip**](https://github.com/CtrlAltDefeat55/Voice_To_Text_Assistant-VTTA/releases/latest/download/VTTA-Windows.zip) and unzip it anywhere (e.g. `Documents\VTTA`).
2. Double-click **`Start VTTA.bat`**.
   - The first start sets up everything inside the folder; it needs internet and takes a few minutes.
   - If Python isn't installed, it offers to install Python 3.12 for your account.
   - At the end it offers a desktop shortcut.
3. After that, start VTTA from the shortcut or `Start VTTA.bat`; it opens straight away. VTTA offers updates itself (status bar → **Update**).

If Windows SmartScreen warns about the file, click **More info → Run anyway**. The `.bat` is plain text, so you can open it in Notepad to see exactly what it does.

The manual steps below are only needed if you'd rather set it up yourself.

## 1) Install prerequisites
- **Python 3.9+** — https://www.python.org/downloads/ (tick “Add python.exe to PATH”; the installer includes Tcl/Tk)
- *(Optional)* **Git** for cloning — https://git-scm.com/
- FFmpeg is **not** required.

## 2) Get the code
- **ZIP:** Download the repo ZIP from GitHub → extract it wherever you like
- **Git:**
```powershell
git clone https://github.com/CtrlAltDefeat55/Voice_To_Text_Assistant-VTTA.git
cd Voice_To_Text_Assistant-VTTA
```

## 3) Create and activate a virtual environment (inside the app folder)
```powershell
py -3 -m venv venv
.\venv\Scripts\activate
```

## 4) Install Python dependencies
```powershell
pip install -r requirements.txt
```
If `pyaudio` fails, try `pip install pipwin`, then `pipwin install pyaudio`.

The private search window uses Playwright's Chromium. Install it from **Settings → Web Search → Install into app folder**, or choose **My default browser** and skip it.

## 5) Run the app
```powershell
python Voice_To_Text_Assistant.py
```
A window titled **“VTTA — Voice to Text Assistant”** opens. The first run downloads the selected Whisper model into `VoiceFlow_App_Files\models`.

To start VTTA automatically, enable **Settings → General → Launch VTTA when I sign in**. It runs without a console window.

## 6) (Optional) Build a single EXE
```powershell
pip install pyinstaller
pyinstaller --name VTTA --onefile --windowed --icon assets\vtta.ico --add-data "assets;assets" Voice_To_Text_Assistant.py
# EXE appears in .\dist\VTTA.exe — its VoiceFlow_App_Files folder is created next to it
```

## Moving the app
Close VTTA (File → Quit), move the whole folder (including `venv`), and start it again from the new place. Settings, history and models come along. If “Launch at sign-in” is on, it updates itself on the next start.

## Troubleshooting
- **`No module named 'tkinter'`** → reinstall Python from python.org with “tcl/tk and IDLE” selected.
- **Mic/permission issues** → Windows Settings → Privacy & security → Microphone → allow desktop apps.
- **Hotkeys stop working** → Tools → Reload Hotkeys (VTTA also does this automatically after sleep).
