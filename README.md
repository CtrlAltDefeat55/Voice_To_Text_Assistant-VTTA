## README.md


# VoiceFlow Assistant 🎤→📝 (Dictation Mode)

A lightweight Tkinter desktop app that records short snippets, transcribes them with Whisper, and (optionally) pastes the text right where your cursor is. It also supports “dictate a web search” and includes a tiny blinking **recording overlay** so you always know what’s happening.

<img width="697" height="879" alt="UI_SpeechtoText" src="https://github.com/user-attachments/assets/8722edb8-3077-4735-916c-e28c364eac9c" />

## ✨ Features
- **One‑tap dictation** with a compact overlay (blink while recording)
- **Paste to cursor** after transcription (toggle)
- **Dictate a web search** → opens results in your browser
- **Self‑healing hotkeys** that re‑register after sleep
- **History (optional)** stored locally in SQLite; off by default
- **Configurable Whisper model & language**
- **Windows Startup integration** (creates a launcher .bat)
- **Voice commands (optional)** — “start/stop dictation” & “search now” using Google’s recognizer; **OFF by default** and configurable in **Settings**.

> ⚠️ This app uses your microphone. Optional voice commands use Google’s online recognizer. See **Security & Privacy** below.

## 🚀 Quick Start

### Windows 10/11
```powershell
# 1) Create and activate a virtual environment
py -3 -m venv venv
.\venv\Scripts\activate

# 2) Install dependencies
pip install -r requirements.txt
python -m playwright install   # installs a compatible browser driver

# 3) (Recommended) Install FFmpeg for Whisper
# Download and add ffmpeg to PATH (check your platform guide)

# 4) Run
python Voice_To_Text_Assistant.py
```

### macOS (Intel/Apple Silicon)
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m playwright install
# Install FFmpeg (e.g., brew install ffmpeg) and PortAudio (brew install portaudio)
python Voice_To_Text_Assistant.py
```

### Linux (Ubuntu/Debian‑like)
```bash
python3 -m venv venv
source venv/bin/activate
sudo apt update && sudo apt install -y ffmpeg python3-tk portaudio19-dev \
  xclip xsel scrot                 # helpers for Tk/Audio/Clipboard/Screenshots
pip install -r requirements.txt
python -m playwright install
python Voice_To_Text_Assistant.py
```
More detail: **[INSTALL-Windows.md](./INSTALL-Windows.md)**

## ⌨️ Default Hotkeys
- **Ctrl+Shift+R** — Start/Stop dictation (paste to cursor if enabled)
- **Ctrl+Shift+S** — Dictate a **search** query (opens results)

> You can change hotkeys and other options in **Settings**.

## 🗣️ Voice Commands (optional)
Voice commands are **OFF by default**. To enable:
1. Open **Settings** → check **Enable Voice Commands (Google recognizer)**.
2. Once enabled, you can say:
   - “**start dictation**” / “**stop dictation**”
   - “**search now**” (starts a quick voice search)
> Note: This uses Google’s online speech recognizer (network access required).


## 🛠️ Troubleshooting
- **`pyaudio` install fails** → On Windows, try `pip install pipwin && pipwin install pyaudio`. On macOS, `brew install portaudio` first. On Linux, install `portaudio19-dev`.
- **No audio device / permissions** → Allow microphone access for your terminal/app.
- **`tkinter` not found** → Install your distro’s Tk package (e.g., `sudo apt install python3-tk`).
- **Playwright won’t open** → Run `python -m playwright install`.

## 🔐 Security & Privacy (short)
- **Mic access** for dictation (local Whisper unless you choose a remote model).
- **Voice commands** (“start dictation”, “stop dictation”, “search now”) use Google’s recognizer over the network.
- **Clipboard & keyboard automation** are used only to paste when you enable “Write to cursor.”
- **Local history** (off by default) is saved to `VoiceFlow_App_Files/history.db`.

See the full policy in [`SECURITY.md`](./SECURITY.md).

## 🤝 Contributing
See [`CONTRIBUTING.md`](./CONTRIBUTING.md). Code of conduct: [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md).
