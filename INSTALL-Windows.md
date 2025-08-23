## INSTALL-Windows.md

# Install & Run — Windows (Voice_To_Text_Assistant Assistant)

These steps set up **Voice To Text Assistant** on **Windows 10/11**.

> This app records audio for short dictations, transcribes with Whisper, and can paste text to your cursor. It can also dictate a web search and open it in your browser.

## 1) Install prerequisites
- **Python 3.9+** — https://www.python.org/downloads/
- *(Optional)* **Git** for cloning — https://git-scm.com/
- *(Recommended)* **FFmpeg** for Whisper — add `ffmpeg.exe` to your PATH

## 2) Get the code
- **ZIP:** Download the repo ZIP from GitHub → extract
- **Git:**
```powershell
git clone https://github.com/<you>/Voice_To_Text_Assistant.git
cd Voice_To_Text_Assistant
```

## 3) Create and activate a virtual environment
```powershell
py -3 -m venv venv
.\venv\Scripts\activate
```
To deactivate later:
```powershell
deactivate
```

## 4) Install Python dependencies
```powershell
pip install -r requirements.txt
python -m playwright install
```
> If `pyaudio` fails, try: `pip install pipwin` then `pipwin install pyaudio`.

## 5) Run the app
```powershell
python Voice_To_Text_Assistant.py
```
A window titled **“VoiceFlow Assistant (Dictation Mode)”** will open.

## 6) (Optional) Build a single EXE
```powershell
pip install pyinstaller
pyinstaller --name VoiceFlowAssistant --onefile --windowed Voice_To_Text_Assistant.py
# EXE appears in .\dist\VoiceFlowAssistant.exe
```

## Troubleshooting
- **`No module named 'tkinter'`** → install the Python installer that includes Tcl/Tk or use the Microsoft Store version carefully.
- **Mic/permission issues** → check Windows Privacy settings → Microphone.
- **Browser doesn’t launch** → ensure `python -m playwright install` ran.
