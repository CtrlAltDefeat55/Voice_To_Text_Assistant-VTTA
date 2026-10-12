# Voice To Text Assistant (VTTA) 🎤→📝

<img src="assets/vtta.png" width="72" align="right" alt="VTTA icon" />

A lightweight desktop dictation helper for **Windows and macOS** (Linux works too). Press a hotkey, speak, press it again — your words are transcribed **locally** with OpenAI Whisper and pasted where your cursor is. You can also dictate a web search.

<img src="assets/screenshot.png" width="560" alt="VTTA main window" />

## ✨ Features
- **Hotkey dictation anywhere** — `Ctrl+Shift+R` to start/stop, text is pasted into the app you were typing in
- **Dictate a web search** — `Ctrl+Shift+S`, results open in a private window or your default browser
- **Start Dictation button** — records until you pause, then shows the text in the window
- **Clear feedback** — status badge, live mic level, a small on-screen indicator while recording/transcribing, and very quiet sound cues (adjustable or off)
- **Runs offline** — speech never leaves your computer (unless you turn on optional voice commands)
- **Low profile** — nothing polls while idle; the microphone is opened only while you record; the model can free its memory after a period of inactivity
- **Portable** — settings, history, models and the optional private browser all live in one folder next to the script
- **History (optional)** — searchable, stored locally in SQLite; off by default
- **Launch at sign-in** (Windows, macOS, Linux) and a tray icon (Windows/Linux) or Dock icon (macOS)

## 🚀 Quick start
Open a terminal **in the project folder**, then:

### Windows 10/11
```powershell
py -3 -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python Voice_To_Text_Assistant.py
```

### macOS (Intel / Apple Silicon)
```bash
brew install portaudio            # needed to build PyAudio
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python Voice_To_Text_Assistant.py
```
Use Python from python.org (includes Tk), or with Homebrew Python also run `brew install python-tk`.

On first use macOS asks for permissions. Allow the app you launch VTTA from (Terminal, or Python) under **System Settings → Privacy & Security**:
- **Microphone** — to record
- **Accessibility** and **Input Monitoring** — for the global hotkeys and for pasting

Restart VTTA after granting them.

### Linux (Ubuntu/Debian-like)
```bash
sudo apt install -y python3-tk portaudio19-dev xclip
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python Voice_To_Text_Assistant.py
```
The `keyboard` package needs root on Linux for global hotkeys; without it, use the window's buttons.

**FFmpeg is no longer needed** — audio goes straight from memory to Whisper.

The first launch downloads the Whisper model you pick (`base` ≈ 140 MB) into `VoiceFlow_App_Files/models`. If you already have it in Whisper's shared cache (`~/.cache/whisper`), it is copied instead of downloaded.

## ⌨️ Using it
| Action | How |
|---|---|
| Dictate & paste | `Ctrl+Shift+R`, speak, `Ctrl+Shift+R` again |
| Dictate a web search | `Ctrl+Shift+S`, speak, `Ctrl+Shift+S` again |
| Switch what happens | Stop with the *other* hotkey (start with R, stop with S → search) |
| Record in the window | **Start Dictation** — stops automatically when you pause |
| Throw a recording away | **Discard** (appears while recording) |
| History / Settings / Quit | `Ctrl+H` / `Ctrl+,` / `Ctrl+Q` (`Cmd` on macOS) |

Change hotkeys in **Settings → General** (click **Record** and press the keys).

If recording stops too early or never stops on its own, adjust **Settings → Recording → Silence threshold**. The dark tick on the mic-level meter shows where it is.

## ⚙️ Settings at a glance
- **General** — hotkeys, sound cues and click sounds with volume and a Test button, recording-indicator position, close-to-tray/Dock, start minimized, launch at sign-in
- **Recording** — microphone choice (listed only when you open Settings or click Refresh), auto-stop pause length and threshold, longest recording, free model memory after N idle minutes
- **Web Search** — private Playwright window or your default browser, install the private browser into the app folder, custom Chromium browser, auto-close timer
- **Advanced** — optional Google voice commands, hotkey timing and indicator timing

## 🗂️ What VTTA stores (all inside the app folder)
```
Voice_To_Text_Assistant.py
assets/                      icon
VoiceFlow_App_Files/
  config.json                settings
  history.db                 only if "Save to history" is on
  models/                    Whisper models you've used
  browsers/                  only if you install the private browser here
start_minimized.bat          Windows only, created when "Launch at sign-in" is on
```
- No log files, temp recordings or caches are written. Problems from the current session are kept in memory (**Tools → Diagnostics**) and cleared on exit.
- The only thing outside the folder is the sign-in entry you opt into: Windows Startup shortcut, macOS LaunchAgent, or Linux autostart file.
- You can move or rename the whole folder. On the next launch VTTA repoints its sign-in entry to the new location. A `venv` created inside the folder moves along with it on Windows. On macOS/Linux, recreate the venv after moving.
- Older versions wrote `VoiceFlow_App_Files/error.log` and `temp_recording.wav`. VTTA no longer creates them, and you can delete them.

## 🛠️ Troubleshooting
- **`pyaudio` install fails** — Windows: `pip install pipwin && pipwin install pyaudio`. macOS: `brew install portaudio`. Linux: `sudo apt install portaudio19-dev`.
- **Hotkeys do nothing** — the hotkey row in the window says why. On macOS grant Accessibility + Input Monitoring. **Tools → Reload Hotkeys** re-registers them.
- **Private search window won't open** — **Settings → Web Search → Install into app folder**, or choose **My default browser**.
- **Something failed** — click **Details** in the status bar (or **Tools → Diagnostics**) and use **Copy All** when reporting an issue.

## 🔐 Privacy
- Audio is recorded only while you dictate, kept in memory, and never saved to disk.
- Transcription runs locally. **Voice commands** (off by default) keep the microphone open and send short clips to Google's online recognizer.
- Pasting uses the clipboard and simulates `Ctrl/Cmd+V` — only after hotkey dictation, and only when **Paste into the active app** is on.

See [`SECURITY.md`](./SECURITY.md).

## 🤝 Contributing
See [`CONTRIBUTING.md`](./CONTRIBUTING.md). Code of conduct: [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md).
