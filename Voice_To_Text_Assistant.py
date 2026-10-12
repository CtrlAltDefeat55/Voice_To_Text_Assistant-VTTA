"""
VTTA - Voice To Text Assistant (Dictation Mode)

Record a short snippet with a global hotkey, transcribe it locally with Whisper,
and paste the text where your cursor is - or dictate a web search.

Works on Windows and macOS (and Linux). Everything the app stores - settings,
history, Whisper models, the optional private browser - lives in the
VoiceFlow_App_Files folder next to this script, so the whole folder can be moved.

Run:  python Voice_To_Text_Assistant.py
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import tkinter.font as tkfont
import threading
import queue
import time
import os
import io
import gc
import sys
import json
import math
import wave
import shutil
import sqlite3
import hashlib
import plistlib
import importlib
import subprocess
import traceback
import webbrowser
import urllib.request
from urllib.parse import quote_plus
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageTk

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
MOD_KEY = "Command" if IS_MAC else "Control"     # Tk binding modifier for in-window shortcuts
MOD_LABEL = "Cmd" if IS_MAC else "Ctrl"

# Optional / platform-sensitive modules. A missing one disables only the feature that needs it.
_IMPORT_ERRORS = {}


def _optional_import(name):
    try:
        return importlib.import_module(name)
    except Exception as e:  # ImportError, or e.g. "keyboard" requiring root on Linux
        _IMPORT_ERRORS[name] = f"{type(e).__name__}: {e}"
        return None


pyaudio = _optional_import("pyaudio")
pyperclip = _optional_import("pyperclip")
pyautogui = _optional_import("pyautogui")
# Global hotkeys: 'keyboard' on Windows/Linux (unchanged), 'pynput' on macOS ('keyboard' needs root there).
keyboard = None if IS_MAC else _optional_import("keyboard")
pynput_keyboard = _optional_import("pynput.keyboard") if IS_MAC else None
# The tray icon is used on Windows/Linux; macOS uses the Dock instead.
pystray = None if IS_MAC else _optional_import("pystray")
# whisper (torch), playwright and speech_recognition are imported lazily, only when needed.

APP_NAME = "VoiceFlowAssistant"          # kept for the existing Startup shortcut name
APP_TITLE = "VTTA — Voice to Text Assistant"
APP_VERSION = "2.0"
DATA_DIR_NAME = "VoiceFlow_App_Files"     # kept so existing config/history are found

SAMPLE_RATE = 16000
CHUNK_SIZE = 1024
SOUND_RATE = 22050
SEARCH_URL = "https://search.brave.com/search?q={}"

LANGUAGES = [
    ("auto", "Auto-detect"), ("en", "English"), ("es", "Spanish"), ("fr", "French"),
    ("de", "German"), ("it", "Italian"), ("pt", "Portuguese"), ("ru", "Russian"),
    ("ja", "Japanese"), ("ko", "Korean"), ("zh", "Chinese"),
]
DEFAULT_MODELS = ["tiny", "base", "small", "medium", "large"]
OVERLAY_POSITIONS = [("bottom-right", "Bottom right"), ("bottom-left", "Bottom left"),
                     ("top-right", "Top right"), ("top-left", "Top left"), ("off", "Don’t show")]

DEFAULT_CONFIG = {
    "transcription_hotkey": "ctrl+shift+r",
    "search_hotkey": "ctrl+shift+s",
    "input_device": "",                  # microphone name; "" = system default
    "silence_duration": 2,               # seconds of silence before auto-stop
    "silence_threshold_pct": 1.0,        # level below this % of full scale counts as silence
    "auto_stop_hotkey": False,           # also auto-stop hotkey recordings on silence
    "max_recording_seconds": 300,
    "unload_model_minutes": 30,          # free the model's memory after this idle time (0 = keep loaded)
    "whisper_model": "base",
    "language": "en",
    "write_to_cursor": True,
    "record_history": False,
    "search_opener": "playwright",       # "playwright" (private window) or "default" (system browser)
    "browser_path": "",
    "browser_timeout_minutes": 60,
    "sound_cues": True,                  # quiet tones for start / stop / done / error
    "click_sounds": True,                # very soft tick when clicking buttons in VTTA
    "sound_volume": 20,                  # percent
    "overlay_position": "bottom-right",
    "hotkey_press_debounce_ms": 250,     # ignore repeats on first press
    "hotkey_post_debounce_ms": 150,      # ignore stray events after transcription finishes
    "overlay_blink_ms": 500,             # blink speed for the overlay LED
    "overlay_flash_top_ms": 800,         # how long overlay stays topmost on each start
    "start_minimized": True,
    "close_to_tray": True,
    "tray_hint_shown": False,
    "startup_target": "",                # last path the sign-in launcher was written for
    "hotkey_self_refresh_minutes": 60,   # periodically re-register hotkeys (0 = only after sleep)
    "wake_gap_seconds": 180,             # if sleep/hibernation gap is detected, refresh
    "enable_voice_commands": False,      # Google voice commands (default OFF)
}

# ---- Visual tokens -------------------------------------------------------------
C = {
    "bg": "#F3F4F7", "surface": "#FFFFFF", "subtle": "#F7F8FA", "border": "#DDE1E8", "border_strong": "#C5CBD6",
    "text": "#161A22", "muted": "#596274", "faint": "#8A91A0",
    "ink": "#1A1E29", "ink_2": "#272D3B", "ink_3": "#353C4D", "ink_text": "#F3F4F8", "ink_muted": "#A2A9BA",
    "accent": "#C93A2B", "accent_hover": "#B23022", "accent_pressed": "#96271B", "accent_soft": "#FCEBE9",
    "coral": "#FF5C4D", "hover": "#EFF1F5", "pressed": "#E2E6EC",
    "disabled_bg": "#ECEEF2", "disabled_fg": "#9AA0AD",
    "danger": "#B42318", "danger_soft": "#FDEDEC", "danger_border": "#EDBDB8",
    "ready": "#2B8A3E", "recording": "#E5483B", "busy": "#D9770B", "loading": "#3B5BDB",
    "error": "#C02626", "meter_bg": "#E7E9EE", "meter_low": "#B4BAC6", "meter_ok": "#2B8A3E",
    "select": "#FBDAD6", "focus": "#3B5BDB", "chip": "#F1F3F7",
}

SOUND_TONES = {  # (frequency Hz, seconds) notes
    "start": [(660, 0.055), (880, 0.07)],
    "stop": [(880, 0.055), (622, 0.07)],
    "done": [(1047, 0.045), (1397, 0.075)],
    "error": [(392, 0.09), (294, 0.13)],
    "click": [(1900, 0.016)],
}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _level_to_frac(level):
    """Map a normalized mean amplitude (0..1) onto a -60..0 dBFS meter scale."""
    db = 20 * math.log10(max(level, 1e-6))
    return _clamp((db + 60.0) / 60.0, 0.0, 1.0)


def _pretty_hotkey(hk):
    names = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Option" if IS_MAC else "Alt", "cmd": "Cmd",
             "command": "Cmd", "windows": "Win", "space": "Space", "esc": "Esc"}
    return "+".join(names.get(p.strip().lower(), p.strip().upper() if len(p.strip()) <= 3 else p.strip().capitalize())
                    for p in str(hk).split("+")) if hk else "—"


def _draw_fallback_icon(size):
    """Used only if assets/vtta.png is missing."""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([s * .04, s * .04, s * .96, s * .96], radius=s * .21, fill=(27, 31, 42, 255))
    d.rounded_rectangle([s * .36, s * .19, s * .64, s * .58], radius=s * .14, fill=(250, 250, 252, 255))
    d.arc([s * .27, s * .32, s * .73, s * .72], 0, 180, fill=(250, 250, 252, 255), width=int(s * .045))
    d.rectangle([s * .478, s * .71, s * .522, s * .82], fill=(250, 250, 252, 255))
    d.arc([s * .19, s * .06, s * .81, s * .68], -32, 32, fill=(255, 92, 77, 255), width=int(s * .05))
    d.arc([s * .19, s * .06, s * .81, s * .68], 148, 212, fill=(255, 92, 77, 255), width=int(s * .05))
    return img.resize((size, size), Image.LANCZOS)


# ======================================================================
# Global hotkey backends
# ======================================================================

class _KeyboardBackend:
    """Windows/Linux: the 'keyboard' package (existing behaviour)."""
    name = "keyboard"
    trusted = True

    def __init__(self):
        self.handles = []

    def register(self, bindings):
        self.unregister()
        for hk, cb in bindings:
            self.handles.append(keyboard.add_hotkey(hk, cb))

    def unregister(self):
        for h in self.handles:
            try:
                keyboard.remove_hotkey(h)
            except Exception:
                pass
        self.handles = []

    def reset(self):
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            try:
                keyboard.unhook_all()
            except Exception:
                pass
        self.handles = []

    def validate(self, hk):
        try:
            keyboard.parse_hotkey(hk)
        except ValueError:
            return "Not a recognized key combination."
        except Exception:
            pass  # can't check here (e.g. key tables unavailable) - registration will tell us
        return None

    def capture(self, done):
        def run():
            try:
                hk = keyboard.read_hotkey(suppress=False)
            except Exception:
                hk = None
            done(hk)
        threading.Thread(target=run, daemon=True).start()

    def shutdown(self):
        try:
            keyboard.unhook_all()
        except Exception:
            pass


class _PynputBackend:
    """macOS: pynput (needs Accessibility / Input Monitoring permission)."""
    name = "pynput"
    MODS = {"ctrl": "<ctrl>", "control": "<ctrl>", "shift": "<shift>", "alt": "<alt>", "option": "<alt>",
            "cmd": "<cmd>", "command": "<cmd>", "win": "<cmd>", "windows": "<cmd>", "super": "<cmd>"}

    def __init__(self):
        self.listener = None
        self.trusted = True

    @classmethod
    def to_pynput(cls, hk):
        parts = []
        for p in hk.lower().split("+"):
            p = p.strip()
            if not p:
                raise ValueError("empty key")
            parts.append(cls.MODS.get(p) or (p if len(p) == 1 else f"<{p}>"))
        return "+".join(parts)

    def register(self, bindings):
        self.unregister()
        mapping = {self.to_pynput(hk): cb for hk, cb in bindings}
        self.listener = pynput_keyboard.GlobalHotKeys(mapping)
        self.listener.daemon = True
        self.listener.start()
        self.listener.wait()
        self.trusted = bool(getattr(self.listener, "IS_TRUSTED", True))

    def unregister(self):
        if self.listener is not None:
            try:
                self.listener.stop()
            except Exception:
                pass
            self.listener = None

    reset = unregister
    shutdown = unregister

    def validate(self, hk):
        try:
            pynput_keyboard.HotKey.parse(self.to_pynput(hk))
            return None
        except Exception:
            return "Not a recognized key combination."

    def capture(self, done):
        mods, result = set(), {}

        def key_name(key):
            if isinstance(key, pynput_keyboard.KeyCode):
                ch = key.char or ""
                if ch and ord(ch[0]) < 32:          # Ctrl+letter arrives as a control character
                    ch = chr(ord(ch[0]) + 96)
                return ch.lower() or None
            return key.name.split("_")[0]           # ctrl_l -> ctrl, f9, space, ...

        def on_press(key):
            n = key_name(key)
            if not n:
                return None
            if n in ("ctrl", "shift", "alt", "cmd"):
                mods.add(n)
                return None
            if n == "esc" and not mods:
                return False
            result["hk"] = "+".join([m for m in ("ctrl", "alt", "shift", "cmd") if m in mods] + [n])
            return False

        def run():
            try:
                with pynput_keyboard.Listener(on_press=on_press) as listener:
                    listener.join()
            except Exception:
                pass
            done(result.get("hk"))
        threading.Thread(target=run, daemon=True).start()


# ======================================================================
# Small helpers
# ======================================================================

class _Recording:
    """One microphone capture. Owned by the recorder thread until it posts itself back."""

    def __init__(self, mode, auto_stop, silence_seconds, threshold, max_seconds, device):
        self.mode = mode                  # "dictate" (paste), "search", or "editor"
        self.auto_stop = auto_stop
        self.silence_seconds = silence_seconds
        self.threshold = threshold        # normalized 0..1
        self.max_seconds = max_seconds
        self.device = device              # microphone name or ""
        self.device_missing = False
        self.stop_event = threading.Event()
        self.discard = False
        self.frames = []
        self.level = 0.0
        self.heard_speech = False
        self.started = time.monotonic()
        self.end_reason = None            # None (stopped), "silence", "no_speech", "limit"
        self.error = None
        self.thread = None


class _Job:
    """One transcription request. Results are matched back to the job that produced them."""
    _next_id = 0

    def __init__(self, audio, mode, language):
        _Job._next_id += 1
        self.id = _Job._next_id
        self.audio = audio
        self.mode = mode
        self.language = language
        self.cancelled = False

    @property
    def seconds(self):
        return len(self.audio) / 2 / SAMPLE_RATE


class _Sounds:
    """Very quiet synthesized cues. Nothing is loaded from or written to disk; a playback thread
    exists only while a sound is playing."""

    def __init__(self, app):
        self.app = app
        self._q = queue.Queue(maxsize=3)
        self._lock = threading.Lock()
        self._worker = None

    @staticmethod
    def render(name, volume):
        notes = []
        for freq, dur in SOUND_TONES[name]:
            t = np.arange(int(SOUND_RATE * dur)) / SOUND_RATE
            env = np.minimum(1.0, t / 0.004) * np.exp(-t * (220 if name == "click" else 28))
            tone = np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(4 * np.pi * freq * t)
            notes += [tone * env, np.zeros(int(SOUND_RATE * 0.012))]
        x = np.concatenate(notes) * 0.32 * (volume ** 1.6)   # perceptual curve; 100% is still modest
        return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()

    def play(self, name, block=False):
        cfg = self.app.config
        if not (cfg.get("click_sounds") if name == "click" else cfg.get("sound_cues")):
            return
        vol = _clamp(float(cfg.get("sound_volume", 20)) / 100.0, 0.0, 1.0)
        if vol <= 0 or self.app._shutting_down:
            return
        pcm = self.render(name, vol)
        if block:
            self._play(pcm)
            return
        try:
            self._q.put_nowait(pcm)
        except queue.Full:
            return
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._drain, name="vtta-sound", daemon=True)
                self._worker.start()

    def _drain(self):
        while True:
            try:
                pcm = self._q.get(timeout=2)   # thread ends after 2 s without sounds
            except queue.Empty:
                return
            self._play(pcm)

    @staticmethod
    def _play(pcm):
        try:
            if IS_WIN:
                import winsound
                buf = io.BytesIO()
                with wave.open(buf, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(SOUND_RATE)
                    w.writeframes(pcm)
                winsound.PlaySound(buf.getvalue(), winsound.SND_MEMORY | winsound.SND_NODEFAULT)
            elif pyaudio:
                pa = pyaudio.PyAudio()
                try:
                    s = pa.open(format=pyaudio.paInt16, channels=1, rate=SOUND_RATE, output=True)
                    s.write(pcm)
                    s.stop_stream()
                    s.close()
                finally:
                    pa.terminate()
        except Exception:
            pass  # sound is a nicety; never let it break anything


class Tooltip:
    def __init__(self, widget, text, delay=500):
        self.widget, self.text, self.delay = widget, text, delay
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._hide()
        self._after = self.widget.after(self.delay, self._show)

    def _show(self):
        text = self.text() if callable(self.text) else self.text
        if not text or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 6
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        try:
            tw.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(tw, text=text, justify=tk.LEFT, bg=C["ink"], fg=C["ink_text"], padx=9, pady=6,
                 wraplength=320, font="TkTooltipFont").pack()
        tw.update_idletasks()
        sw = tw.winfo_screenwidth()
        x = min(x, sw - tw.winfo_reqwidth() - 8)
        tw.wm_geometry(f"+{x}+{y}")

    def _hide(self, _e=None):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


# ======================================================================
# Application
# ======================================================================

class VoiceFlowAssistant:
    def __init__(self):
        if IS_WIN:
            try:  # crisp text on scaled displays, and our own taskbar icon instead of Python's
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VTTA.VoiceToTextAssistant")
            except Exception:
                pass

        self.root = tk.Tk(className="VTTA")
        self.root.withdraw()  # build fully before the first paint
        self.root.title(APP_TITLE)
        self._scale = _clamp(self.root.winfo_fpixels("1i") / 96.0, 1.0, 3.0)

        # --- Paths: everything app-owned lives beside the script/exe ---
        if getattr(sys, "frozen", False):
            self.script_dir = Path(sys.executable).parent.resolve()
        else:
            self.script_dir = Path(__file__).parent.resolve()
        self.resource_dir = Path(getattr(sys, "_MEIPASS", self.script_dir))
        self.app_data_dir = self.script_dir / DATA_DIR_NAME
        self.app_data_dir.mkdir(exist_ok=True)
        self.models_dir = self.app_data_dir / "models"
        self.browsers_dir = self.app_data_dir / "browsers"
        self.config_file = self.app_data_dir / "config.json"
        self.history_db = self.app_data_dir / "history.db"
        self.launcher_bat = self.script_dir / "start_minimized.bat"  # auto (re)generated for Windows Startup

        # --- Runtime state ---
        self._ui_q = queue.Queue()
        self._drain_pending = False
        self._shutdown = threading.Event()
        self._voice_wake = threading.Event()   # wakes the voice-command thread when enabled or on exit
        self._shutting_down = False
        self._rec = None                  # active _Recording
        self._pending_jobs = deque()      # queued/running _Job, in order
        self._failed_job = None           # last job that failed, for Retry
        self._tasks = queue.Queue()       # model worker: ("load", name) | ("transcribe", job) | ("unload",) | None
        self._model = None
        self._model_name = None
        self._wanted_model = None
        self._model_state = "loading"     # loading | downloading | copying | ready | unloaded | error
        self._model_progress = None
        self._unload_after_id = None
        self._error_flag = False
        self._browser_threads = set()
        self._child_procs = set()         # app-owned subprocesses (browser install) to stop on exit
        self._capturing_hotkey = False
        self._hotkeys_ok = False
        self._hotkey_error = None
        self._diagnostics = deque(maxlen=200)   # in-memory only
        self._tray_icon = None
        self._tray_ok = False
        self._tray_images = {}
        self._tray_state = None
        self._history_win = None
        self._diag_win = None
        self._settings_win = None
        self._tick_id = None
        self._flash_ids = {}
        self.hotkeys = None

        # Debounce control (monotonic times)
        self.debounce_until = 0.0
        self.debounce_press_until = 0.0

        # Overlay state
        self.overlay_win = None
        self.overlay_canvas = None
        self.overlay_state = None
        self.overlay_text = ("", "")
        self._overlay_hint = ""
        self.overlay_blink_after_id = None
        self.overlay_blink_on = True
        self.overlay_top_flash_after_id = None
        self.overlay_hide_after_id = None

        self.load_config()
        self.init_database()
        self._cleanup_stale_downloads()
        self.sounds = _Sounds(self)

        self._setup_dispatch()
        self._load_icons()
        self._build_fonts()
        self._build_theme()
        self.create_widgets()
        self._build_menu()
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close_window)

        threading.Thread(target=self._model_worker, name="vtta-model", daemon=True).start()
        self.request_model(self.config["whisper_model"])

        self._init_hotkeys()
        self.start_hotkey_watchdog()
        self.start_voice_command_listener()
        self._start_tray()
        threading.Thread(target=self._sync_startup_entry, daemon=True).start()

        self._refresh_state()
        self.root.deiconify()
        if self.config.get("start_minimized", True):
            self.root.iconify()

    # ======================================================================
    # Thread plumbing (event-driven: nothing polls while the app is idle)
    # ======================================================================

    def px(self, n):
        return int(round(n * self._scale))

    def _setup_dispatch(self):
        try:
            self._threaded_tcl = bool(int(self.root.tk.eval(
                "expr {[info exists tcl_platform(threaded)] && $tcl_platform(threaded)}")))
        except Exception:
            self._threaded_tcl = False
        self.root.bind("<<VTTAPost>>", lambda e: self._drain_ui_queue())
        if not self._threaded_tcl:      # rare non-threaded Tcl builds: fall back to a slow poll
            self._fallback_poll()

    def _fallback_poll(self):
        self._drain_ui_queue()
        if not self._shutting_down:
            self.root.after(100, self._fallback_poll)

    def _post(self, fn, *args):
        """Run fn(*args) on the Tk thread. Safe from any thread; dropped after shutdown."""
        if self._shutting_down:
            return
        self._ui_q.put((fn, args))
        if self._on_main():
            if not self._drain_pending:
                self._drain_pending = True
                self.root.after_idle(self._drain_ui_queue)
        elif self._threaded_tcl:
            try:
                self.root.event_generate("<<VTTAPost>>", when="tail")
            except Exception:
                pass  # e.g. before the main loop starts; drained once it does

    def _drain_ui_queue(self):
        self._drain_pending = False
        while True:
            try:
                fn, args = self._ui_q.get_nowait()
            except queue.Empty:
                return
            try:
                fn(*args)
            except Exception as e:
                if not self._shutting_down:
                    self.handle_error("Internal error", "Something went wrong updating the window.",
                                      details=e, show_message=False)

    def _on_main(self):
        return threading.current_thread() is threading.main_thread()

    # ======================================================================
    # Look & feel
    # ======================================================================

    def _load_icons(self):
        path = self.resource_dir / "assets" / "vtta.png"
        try:
            base = Image.open(path).convert("RGBA")
        except Exception:
            base = _draw_fallback_icon(256)
        self._icon_base = base
        self._icon_photos = [ImageTk.PhotoImage(base.resize((s, s), Image.LANCZOS)) for s in (256, 64, 32, 16)]
        try:
            self.root.iconphoto(True, *self._icon_photos)
        except Exception:
            pass
        ico = self.resource_dir / "assets" / "vtta.ico"
        if IS_WIN and ico.exists():
            try:
                self.root.iconbitmap(default=str(ico))
            except Exception:
                pass
        hs = self.px(32)
        self._header_icon = ImageTk.PhotoImage(base.resize((hs, hs), Image.LANCZOS))

    def _build_fonts(self):
        base = 13 if IS_MAC else 10
        default = tkfont.nametofont("TkDefaultFont")
        family = "Segoe UI" if IS_WIN else default.actual("family")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=family, size=base)
            except Exception:
                pass
        try:
            tkfont.nametofont("TkTooltipFont").configure(family=family, size=base - 1)
        except Exception:
            pass
        self.f_body = tkfont.Font(family=family, size=base)
        self.f_small = tkfont.Font(family=family, size=base - 1)
        self.f_bold = tkfont.Font(family=family, size=base, weight="bold")
        self.f_small_bold = tkfont.Font(family=family, size=base - 1, weight="bold")
        self.f_button = tkfont.Font(family=family, size=base, weight="bold")
        self.f_section = tkfont.Font(family=family, size=base + 1, weight="bold")
        self.f_title = tkfont.Font(family=family, size=base + 4, weight="bold")
        self.f_editor = tkfont.Font(family=family, size=base + 2)
        mono = "Consolas" if IS_WIN else ("Menlo" if IS_MAC else "TkFixedFont")
        self.f_mono = tkfont.Font(family=mono, size=base - 1)
        self.f_chip = tkfont.Font(family=family, size=base - 1, weight="bold")
        self.f_overlay_title = tkfont.Font(family=family, size=base + 1, weight="bold")
        self.f_overlay_sub = tkfont.Font(family=family, size=base - 1)

    def _indicator_images(self):
        """Crisp custom checkbox / radio indicators (the stock ones look dated)."""
        s = self.px(17)
        gap = self.px(7)

        def make(kind, on, hover, disabled):
            S = s * 4
            img = Image.new("RGBA", ((s + gap) * 4, S), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            border = C["border_strong"] if not hover else C["accent"]
            fill = C["surface"]
            if disabled:
                border, fill = "#D3D7DE", C["disabled_bg"]
            if on:
                fill = border = (C["accent"] if not hover else C["accent_hover"]) if not disabled else "#C3C7CF"
            box = [2, 2, S - 3, S - 3]
            if kind == "check":
                d.rounded_rectangle(box, radius=S * 0.24, fill=fill, outline=border, width=6)
                if on:
                    d.line([(S * .26, S * .52), (S * .43, S * .69), (S * .75, S * .33)], fill="#FFFFFF",
                           width=int(S * .12), joint="curve")
            else:
                d.ellipse(box, fill=C["surface"] if not disabled else C["disabled_bg"],
                          outline=border if not on else fill, width=6 if not on else int(S * .1))
                if on:
                    r = S * 0.2
                    d.ellipse([S / 2 - r, S / 2 - r, S / 2 + r, S / 2 + r], fill=fill)
            return ImageTk.PhotoImage(img.resize((s + gap, s), Image.LANCZOS))

        imgs = {}
        for kind in ("check", "radio"):
            for on in (False, True):
                imgs[(kind, on, False, False)] = make(kind, on, False, False)
                imgs[(kind, on, True, False)] = make(kind, on, True, False)
                imgs[(kind, on, False, True)] = make(kind, on, False, True)
        self._indicator_imgs = imgs
        return imgs

    def _build_theme(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        px = self.px
        self.root.configure(bg=C["bg"])
        st.configure(".", background=C["bg"], foreground=C["text"], font=self.f_body,
                     bordercolor=C["border"], lightcolor=C["bg"], darkcolor=C["bg"],
                     troughcolor=C["meter_bg"], focuscolor=C["focus"],
                     selectbackground=C["select"], selectforeground=C["text"], insertcolor=C["text"])

        imgs = self._indicator_images()
        for kind, cls in (("check", "Checkbutton"), ("radio", "Radiobutton")):
            elem = f"VTTA.{cls}.indicator"
            st.element_create(
                elem, "image", imgs[(kind, False, False, False)],
                ("disabled", "selected", imgs[(kind, True, False, True)]),
                ("disabled", imgs[(kind, False, False, True)]),
                ("active", "selected", imgs[(kind, True, True, False)]),
                ("selected", imgs[(kind, True, False, False)]),
                ("active", imgs[(kind, False, True, False)]),
                sticky="")
            layout = [(f"{cls}.padding", {"sticky": "nswe", "children": [
                (elem, {"side": "left", "sticky": ""}),
                (f"{cls}.focus", {"side": "left", "sticky": "w", "children": [
                    (f"{cls}.label", {"sticky": "nswe"})]})]})]
            for prefix in ("", "Card."):
                st.layout(f"{prefix}T{cls}", layout)

        for surf, name in ((C["bg"], ""), (C["surface"], "Card.")):
            st.configure(f"{name}TFrame", background=surf)
            st.configure(f"{name}TLabel", background=surf, foreground=C["text"])
            st.configure(f"{name}Muted.TLabel", background=surf, foreground=C["muted"], font=self.f_small)
            st.configure(f"{name}Section.TLabel", background=surf, foreground=C["text"], font=self.f_section)
            st.configure(f"{name}Error.TLabel", background=surf, foreground=C["danger"], font=self.f_small)
            for cls in ("TCheckbutton", "TRadiobutton"):
                st.configure(f"{name}{cls}", background=surf, foreground=C["text"], focuscolor=surf,
                             padding=(0, px(2)))
                st.map(f"{name}{cls}", background=[("active", surf)],
                       foreground=[("disabled", C["disabled_fg"]), ("active", C["text"])],
                       focuscolor=[("focus", C["focus"])])

        pad = (px(14), px(7))

        def button(name, bg, fg, hover, pressed, border=None, font=None, padding=pad, hover_fg=None):
            solid = border is not None
            border = border or bg
            st.configure(name, background=bg, foreground=fg, bordercolor=border, lightcolor=bg,
                         darkcolor=bg, relief="solid" if solid else "flat", borderwidth=1, width=0,
                         padding=padding, font=font or self.f_button, focusthickness=1, focuscolor=fg,
                         anchor="center")
            st.map(name,
                   background=[("disabled", C["disabled_bg"]), ("pressed", pressed), ("active", hover)],
                   lightcolor=[("disabled", C["disabled_bg"]), ("pressed", pressed), ("active", hover)],
                   darkcolor=[("disabled", C["disabled_bg"]), ("pressed", pressed), ("active", hover)],
                   foreground=[("disabled", C["disabled_fg"])] + ([("active", hover_fg)] if hover_fg else []),
                   bordercolor=[("disabled", C["disabled_bg"] if not solid else C["border"]),
                                ("focus", C["focus"]), ("active", border if not solid else C["faint"])])

        big = (px(20), px(10))
        button("Primary.TButton", C["accent"], "#FFFFFF", C["accent_hover"], C["accent_pressed"], padding=big)
        button("Stop.TButton", C["ink"], "#FFFFFF", C["ink_3"], "#0E1117", padding=big)
        button("TButton", C["surface"], C["text"], C["hover"], C["pressed"], border=C["border_strong"],
               font=self.f_body)
        button("Secondary.TButton", C["surface"], C["text"], C["hover"], C["pressed"],
               border=C["border_strong"], padding=big)
        button("Small.TButton", C["surface"], C["text"], C["hover"], C["pressed"], border=C["border"],
               font=self.f_small, padding=(px(10), px(4)))
        button("Tertiary.TButton", C["surface"], C["muted"], C["hover"], C["pressed"], font=self.f_body,
               padding=(px(10), px(5)), hover_fg=C["text"])
        button("Link.TButton", C["surface"], C["accent"], C["accent_soft"], C["select"], font=self.f_small_bold,
               padding=(px(8), px(3)))
        button("Danger.TButton", C["surface"], C["danger"], C["danger_soft"], "#F8D7D7",
               border=C["danger_border"], font=self.f_small, padding=(px(10), px(4)))
        button("Header.TButton", C["ink"], C["ink_text"], C["ink_3"], "#0E1117", font=self.f_small_bold,
               padding=(px(10), px(5)), border=C["ink_3"])

        field = dict(fieldbackground=C["surface"], bordercolor=C["border_strong"], lightcolor=C["surface"],
                     darkcolor=C["surface"])
        st.configure("TEntry", padding=(px(7), px(5)), **field)
        st.map("TEntry", bordercolor=[("focus", C["focus"]), ("hover", C["faint"])],
               lightcolor=[("focus", C["focus"])],
               fieldbackground=[("disabled", C["disabled_bg"])], foreground=[("disabled", C["disabled_fg"])])
        st.configure("Invalid.TEntry", bordercolor=C["danger"], lightcolor=C["danger"])
        st.configure("TSpinbox", arrowsize=px(11), padding=(px(7), px(4)), arrowcolor=C["muted"],
                     background=C["surface"], **field)
        st.map("TSpinbox", bordercolor=[("focus", C["focus"]), ("hover", C["faint"])],
               lightcolor=[("focus", C["focus"])], arrowcolor=[("disabled", C["disabled_fg"])],
               background=[("active", C["hover"])],
               fieldbackground=[("disabled", C["disabled_bg"])], foreground=[("disabled", C["disabled_fg"])])
        st.configure("Invalid.TSpinbox", bordercolor=C["danger"], lightcolor=C["danger"])
        st.configure("TCombobox", background=C["surface"], arrowsize=px(13), arrowcolor=C["muted"],
                     padding=(px(7), px(4)), **field)
        st.map("TCombobox", fieldbackground=[("readonly", C["surface"]), ("disabled", C["disabled_bg"])],
               background=[("active", C["hover"]), ("readonly", C["surface"])],
               bordercolor=[("focus", C["focus"]), ("hover", C["faint"])], lightcolor=[("focus", C["focus"])],
               selectbackground=[("readonly", C["surface"])], selectforeground=[("readonly", C["text"])],
               foreground=[("disabled", C["disabled_fg"])])
        self.root.option_add("*TCombobox*Listbox.font", self.f_body)
        self.root.option_add("*TCombobox*Listbox.background", C["surface"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", C["select"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", C["text"])
        self.root.option_add("*TCombobox*Listbox.borderWidth", 0)

        st.configure("Horizontal.TScale", background=C["accent"], troughcolor=C["meter_bg"],
                     bordercolor=C["surface"], lightcolor=C["accent"], darkcolor=C["accent"],
                     sliderlength=px(14), sliderthickness=px(14), troughrelief="flat", gripcount=0,
                     sliderrelief="flat")
        st.map("Horizontal.TScale", background=[("active", C["accent_hover"])])

        st.configure("TNotebook", background=C["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        st.configure("TNotebook.Tab", background=C["bg"], foreground=C["muted"], bordercolor=C["border"],
                     lightcolor=C["bg"], padding=(px(16), px(7)), font=self.f_body)
        st.map("TNotebook.Tab", background=[("selected", C["surface"]), ("active", C["hover"])],
               foreground=[("selected", C["text"]), ("active", C["text"])],
               lightcolor=[("selected", C["surface"])], font=[("selected", self.f_bold)])
        st.configure("Treeview", background=C["surface"], fieldbackground=C["surface"],
                     rowheight=self.f_body.metrics("linespace") + px(10), bordercolor=C["border"],
                     lightcolor=C["surface"], darkcolor=C["surface"])
        st.map("Treeview", background=[("selected", C["select"])], foreground=[("selected", C["text"])])
        st.configure("Treeview.Heading", background=C["subtle"], foreground=C["muted"], font=self.f_small_bold,
                     relief="flat", padding=(px(8), px(5)), bordercolor=C["border"], lightcolor=C["subtle"],
                     darkcolor=C["subtle"])
        st.map("Treeview.Heading", background=[("active", C["hover"])])
        st.configure("Busy.Horizontal.TProgressbar", troughcolor=C["meter_bg"], background=C["busy"],
                     bordercolor=C["meter_bg"], lightcolor=C["busy"], darkcolor=C["busy"], thickness=px(4))
        st.configure("Model.Horizontal.TProgressbar", troughcolor=C["meter_bg"], background=C["loading"],
                     bordercolor=C["meter_bg"], lightcolor=C["loading"], darkcolor=C["loading"], thickness=px(4))
        # Slim, arrowless scrollbars
        for orient in ("Vertical", "Horizontal"):
            st.layout(f"Slim.{orient}.TScrollbar", [(f"{orient}.Scrollbar.trough", {
                "sticky": "ns" if orient == "Vertical" else "we",
                "children": [(f"{orient}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
            st.configure(f"Slim.{orient}.TScrollbar", troughcolor=C["surface"], background="#CDD2DB",
                         bordercolor=C["surface"], lightcolor="#CDD2DB", darkcolor="#CDD2DB",
                         arrowsize=px(9), gripcount=0)
            st.map(f"Slim.{orient}.TScrollbar", background=[("pressed", "#9AA1AE"), ("active", "#B2B8C4")],
                   lightcolor=[("pressed", "#9AA1AE"), ("active", "#B2B8C4")],
                   darkcolor=[("pressed", "#9AA1AE"), ("active", "#B2B8C4")])

        # Hand cursor + soft click sound on every clickable ttk control
        for cls in ("TButton", "TCheckbutton", "TRadiobutton", "TCombobox", "TNotebook"):
            self.root.bind_class(cls, "<Enter>", self._on_hover_enter, add="+")
            self.root.bind_class(cls, "<ButtonRelease-1>", self._on_click_feedback, add="+")

    def _on_hover_enter(self, e):
        try:
            if str(e.widget.cget("cursor")) == "":
                e.widget.configure(cursor="hand2")
        except Exception:
            pass

    def _on_click_feedback(self, e):
        w = e.widget
        try:
            if w.instate(["disabled"]):
                return
            if not (0 <= e.x < w.winfo_width() and 0 <= e.y < w.winfo_height()):
                return
        except Exception:
            return
        self.sounds.play("click")

    def _card(self, parent, **pack):
        f = tk.Frame(parent, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"],
                     highlightcolor=C["border"])
        if pack:
            f.pack(**pack)
        return f

    def _chip(self, parent, text):
        return tk.Label(parent, text=text, font=self.f_chip, bg=C["chip"], fg=C["text"], padx=self.px(7),
                        pady=self.px(1), highlightthickness=1, highlightbackground=C["border"])

    def center_window(self, win, w, h):
        win.update_idletasks()
        if self._window_visible():
            x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
            y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - h) // 3)
        else:
            x = (win.winfo_screenwidth() - w) // 2
            y = (win.winfo_screenheight() - h) // 3
        x = _clamp(x, 0, max(0, win.winfo_screenwidth() - w))
        y = _clamp(y, 0, max(0, win.winfo_screenheight() - h))
        win.geometry(f"{w}x{h}+{x}+{y}")

    def flash_button(self, btn, text, ms=1300):
        """Briefly confirm an action on the button itself (e.g. Copy -> Copied)."""
        key = str(btn)
        if key not in self._flash_ids:
            original = btn.cget("text")
        else:
            original, aid = self._flash_ids.pop(key)
            self.root.after_cancel(aid)
        btn.configure(text=text)

        def restore():
            self._flash_ids.pop(key, None)
            if btn.winfo_exists():
                btn.configure(text=original)
        self._flash_ids[key] = (original, self.root.after(ms, restore))

    # ======================================================================
    # Main window
    # ======================================================================

    def create_widgets(self):
        px = self.px
        self.root.geometry(f"{px(680)}x{px(640)}")
        self.root.minsize(px(560), px(520))

        # ---- Header band ----
        header = tk.Frame(self.root, bg=C["ink"])
        header.pack(fill=tk.X)
        hin = tk.Frame(header, bg=C["ink"])
        hin.pack(fill=tk.X, padx=px(18), pady=px(12))
        tk.Label(hin, image=self._header_icon, bg=C["ink"]).pack(side=tk.LEFT)
        titles = tk.Frame(hin, bg=C["ink"])
        titles.pack(side=tk.LEFT, padx=(px(11), 0))
        tk.Label(titles, text="VTTA", font=self.f_title, bg=C["ink"], fg=C["ink_text"]).pack(anchor=tk.W)
        tk.Label(titles, text="Voice to Text Assistant", font=self.f_small, bg=C["ink"],
                 fg=C["ink_muted"]).pack(anchor=tk.W)

        settings_btn = ttk.Button(hin, text="⚙  Settings", style="Header.TButton", command=self.open_settings)
        settings_btn.pack(side=tk.RIGHT, padx=(px(10), 0))
        Tooltip(settings_btn, f"Hotkeys, microphone, sounds, search and more ({MOD_LABEL}+,)")
        pill = tk.Frame(hin, bg=C["ink_2"], padx=px(11), pady=px(5), highlightthickness=1,
                        highlightbackground=C["ink_3"])
        pill.pack(side=tk.RIGHT)
        self.state_dot = tk.Canvas(pill, width=px(10), height=px(10), bg=C["ink_2"], highlightthickness=0)
        self.state_dot.pack(side=tk.LEFT, padx=(0, px(7)))
        self._state_dot_id = self.state_dot.create_oval(1, 1, px(10) - 1, px(10) - 1, fill=C["loading"],
                                                        outline="")
        self.state_label = tk.Label(pill, text="Starting…", font=self.f_small_bold, bg=C["ink_2"],
                                    fg=C["ink_text"])
        self.state_label.pack(side=tk.LEFT)

        # ---- Status bar (packed early so it keeps its space when the window shrinks) ----
        sbar = tk.Frame(self.root, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
        sbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="Starting…")
        self.status_time = tk.Label(sbar, text="", font=self.f_small, bg=C["surface"], fg=C["faint"])
        self.status_time.pack(side=tk.LEFT, padx=(px(14), px(6)), pady=px(6))
        self.status_bar = tk.Label(sbar, textvariable=self.status_var, anchor=tk.W, font=self.f_small,
                                   bg=C["surface"], fg=C["muted"])
        self.status_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=px(6))
        self.retry_btn = ttk.Button(sbar, text="Retry transcription", style="Link.TButton",
                                    command=self.retry_failed_job)
        self.details_btn = ttk.Button(sbar, text="Details", style="Link.TButton", command=self.show_diagnostics)

        body = ttk.Frame(self.root)
        body.pack(fill=tk.BOTH, expand=True, padx=px(18), pady=(px(16), px(12)))

        # ---- Capture card: primary actions, live level, hotkeys ----
        cap = self._card(body, fill=tk.X)
        cin = ttk.Frame(cap, style="Card.TFrame", padding=(px(16), px(14), px(16), px(12)))
        cin.pack(fill=tk.X)
        row = self._action_row = ttk.Frame(cin, style="Card.TFrame")
        row.pack(fill=tk.X)
        self.start_button = ttk.Button(row, text="●  Start Dictation", style="Primary.TButton",
                                       command=self.on_primary_button)
        self.start_button.pack(side=tk.LEFT)
        Tooltip(self.start_button, lambda: (
            "Stop recording and transcribe it." if self._rec else
            "Record from your microphone. Stops by itself after "
            f"{self._fmt_num(self.config['silence_duration'])} s of silence, or click again to stop. "
            "The text appears below; nothing is pasted elsewhere."))
        self.discard_button = ttk.Button(row, text="Discard", style="Tertiary.TButton",
                                         command=self.on_discard_button)
        Tooltip(self.discard_button, lambda: "Stop recording and throw the audio away." if self._rec
                else "Cancel the pending transcription and discard its result.")
        self.search_button = ttk.Button(row, text="Search Web", style="Secondary.TButton",
                                        command=self.perform_search_from_button)
        self.search_button.pack(side=tk.LEFT, padx=(px(8), 0))
        Tooltip(self.search_button, lambda: "Search the web for the transcript text." if self._editor_text()
                else "Type or dictate some text first, then search the web for it.")

        meter_box = ttk.Frame(row, style="Card.TFrame")
        meter_box.pack(side=tk.RIGHT, fill=tk.Y)
        mtop = ttk.Frame(meter_box, style="Card.TFrame")
        mtop.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(mtop, text="Mic level", style="Card.Muted.TLabel").pack(side=tk.LEFT)
        self.timer_label = ttk.Label(mtop, text="", style="Card.Muted.TLabel", anchor=tk.E)
        self.timer_label.pack(side=tk.RIGHT)
        self.meter = tk.Canvas(meter_box, width=px(170), height=px(8), bg=C["surface"], highlightthickness=0)
        self.meter.pack(side=tk.TOP, pady=(px(5), 0))
        mw, mh = px(170), px(8)
        self.meter.create_rectangle(0, 0, mw, mh, fill=C["meter_bg"], outline="")
        self._meter_bar = self.meter.create_rectangle(0, 0, 0, mh, fill=C["meter_low"], outline="")
        self._meter_tick = self.meter.create_rectangle(0, 0, 0, mh, fill=C["ink"], outline="")
        self.meter_hint = ttk.Label(meter_box, text="Live while recording", style="Card.Muted.TLabel")
        self.meter_hint.pack(side=tk.TOP, anchor=tk.E, pady=(px(3), 0))
        for w in (self.meter, self.meter_hint):
            Tooltip(w, lambda: (
                "Microphone level, shown only while recording. The dark tick marks the silence threshold "
                f"({self._fmt_num(self.config['silence_threshold_pct'])}% of full scale): sound below it counts "
                "as silence for auto-stop. Adjust it in Settings → Recording."))

        self.busy_bar = ttk.Progressbar(cin, mode="indeterminate", style="Busy.Horizontal.TProgressbar")

        tk.Frame(cin, bg=C["border"], height=1).pack(fill=tk.X, pady=(px(14), px(10)))
        self.hk_row = ttk.Frame(cin, style="Card.TFrame")
        self.hk_row.pack(fill=tk.X)
        self._build_hotkey_row()

        # ---- Transcript card ----
        tcard = self._card(body, fill=tk.BOTH, expand=True, pady=(px(14), 0))
        tin = ttk.Frame(tcard, style="Card.TFrame", padding=(px(16), px(12), px(16), px(12)))
        tin.pack(fill=tk.BOTH, expand=True)
        thead = ttk.Frame(tin, style="Card.TFrame")
        thead.pack(fill=tk.X)
        ttk.Label(thead, text="Transcript", style="Card.Section.TLabel").pack(side=tk.LEFT)
        self.word_count = ttk.Label(thead, text="", style="Card.Muted.TLabel")
        self.word_count.pack(side=tk.LEFT, padx=(px(8), 0), pady=(px(2), 0))
        hist_btn = ttk.Button(thead, text="History", style="Small.TButton", command=self.show_history)
        hist_btn.pack(side=tk.RIGHT)
        Tooltip(hist_btn, f"Browse, search and reuse saved transcripts ({MOD_LABEL}+H)")
        self.clear_btn = ttk.Button(thead, text="Clear", style="Small.TButton", command=self.clear_editor)
        self.clear_btn.pack(side=tk.RIGHT, padx=(0, px(6)))
        Tooltip(self.clear_btn, f"Clear the transcript box ({MOD_LABEL}+Z undoes)")
        self.copy_btn = ttk.Button(thead, text="Copy", style="Small.TButton", command=self.copy_editor)
        self.copy_btn.pack(side=tk.RIGHT, padx=(0, px(6)))
        Tooltip(self.copy_btn, "Copy the transcript to the clipboard")

        ed = tk.Frame(tin, bg=C["surface"], highlightthickness=1, highlightbackground=C["border_strong"],
                      highlightcolor=C["focus"])
        ed.pack(fill=tk.BOTH, expand=True, pady=(px(10), px(10)))
        self.text_editor = tk.Text(ed, wrap=tk.WORD, undo=True, maxundo=50, font=self.f_editor, relief="flat",
                                   bd=0, padx=px(12), pady=px(10), bg=C["surface"], fg=C["text"],
                                   insertbackground=C["text"], selectbackground=C["select"],
                                   selectforeground=C["text"], height=6, highlightthickness=0,
                                   spacing2=px(3))
        sb = ttk.Scrollbar(ed, orient=tk.VERTICAL, command=self.text_editor.yview,
                           style="Slim.Vertical.TScrollbar")
        self.text_editor.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, px(2)), pady=px(2))
        self.text_editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.text_editor.bind("<<Modified>>", self._on_editor_modified)
        self.placeholder = tk.Label(self.text_editor, text="Your transcript appears here. You can also type or "
                                    "paste text to search for it.", bg=C["surface"], fg=C["faint"],
                                    font=self.f_body, anchor="nw", justify=tk.LEFT)
        self.placeholder.bind("<Button-1>", lambda e: self.text_editor.focus_set())

        opts = ttk.Frame(tin, style="Card.TFrame")
        opts.pack(fill=tk.X)
        self.write_to_cursor_var = tk.BooleanVar(value=bool(self.config["write_to_cursor"]))
        self.record_history_var = tk.BooleanVar(value=bool(self.config["record_history"]))
        cb = ttk.Checkbutton(opts, text="Paste into the active app after hotkey dictation",
                             style="Card.TCheckbutton", variable=self.write_to_cursor_var,
                             command=lambda: self._set_config("write_to_cursor", self.write_to_cursor_var.get()))
        cb.pack(side=tk.LEFT)
        Tooltip(cb, f"When you dictate with the hotkey, the text is copied to the clipboard and pasted "
                    f"({MOD_LABEL}+V) into whichever window has focus. The transcript box always gets a copy.")
        hb = ttk.Checkbutton(opts, text="Save to history", style="Card.TCheckbutton",
                             variable=self.record_history_var,
                             command=lambda: self._set_config("record_history", self.record_history_var.get()))
        hb.pack(side=tk.LEFT, padx=(px(18), 0))
        Tooltip(hb, "Keep transcripts and searches in a local database (history.db in the app folder). "
                    "Nothing is saved while this is off.")

        # ---- Transcription engine row ----
        eng = ttk.Frame(body)
        eng.pack(fill=tk.X, pady=(px(14), 0))
        ttk.Label(eng, text="Model", style="Muted.TLabel").pack(side=tk.LEFT)
        self.model_var = tk.StringVar(value=self.config["whisper_model"])
        self.model_combo = ttk.Combobox(eng, textvariable=self.model_var, state="readonly", width=11,
                                        values=self._model_choices())
        self.model_combo.pack(side=tk.LEFT, padx=(px(6), px(16)))
        self.model_combo.bind("<<ComboboxSelected>>", self.change_model)
        Tooltip(self.model_combo, "Whisper model used for transcription. Larger models are more accurate "
                                  "but slower and use more memory. Each model downloads once into "
                                  f"{DATA_DIR_NAME}/models.")
        ttk.Label(eng, text="Language", style="Muted.TLabel").pack(side=tk.LEFT)
        self.lang_var = tk.StringVar(value=self._lang_display(self.config["language"]))
        lang_combo = ttk.Combobox(eng, textvariable=self.lang_var, state="readonly", width=15,
                                  values=[self._lang_display(c) for c, _ in LANGUAGES])
        lang_combo.pack(side=tk.LEFT, padx=(px(6), 0))
        lang_combo.bind("<<ComboboxSelected>>", self.change_language)
        Tooltip(lang_combo, "Spoken language. Auto-detect handles mixed use but is a little slower "
                            "and can guess wrong on very short clips.")
        self.model_bar = ttk.Progressbar(eng, mode="determinate", length=px(110), maximum=1.0,
                                         style="Model.Horizontal.TProgressbar")
        mrow = ttk.Frame(body)
        mrow.pack(fill=tk.X, pady=(px(6), 0))
        self.model_status = ttk.Label(mrow, text="", style="Muted.TLabel")
        self.model_status.pack(side=tk.LEFT)

        self._on_editor_modified()
        self._draw_meter(0.0)

    def _build_hotkey_row(self):
        for w in self.hk_row.winfo_children():
            w.destroy()
        px = self.px
        if self._hotkeys_ok or self.hotkeys is None and not self._hotkey_error:
            ttk.Label(self.hk_row, text="Hotkeys", style="Card.Muted.TLabel").pack(side=tk.LEFT, padx=(0, px(10)))
            for key, label in (("transcription_hotkey", "dictate & paste"), ("search_hotkey", "dictate a search")):
                self._chip(self.hk_row, _pretty_hotkey(self.config[key])).pack(side=tk.LEFT)
                ttk.Label(self.hk_row, text=label, style="Card.Muted.TLabel").pack(side=tk.LEFT,
                                                                                   padx=(px(6), px(16)))
            ttk.Label(self.hk_row, text="work in any app", style="Card.Muted.TLabel").pack(side=tk.RIGHT)
        else:
            reason = self._hotkey_error or "not registered"
            ttk.Label(self.hk_row, text=f"⚠  Global hotkeys are off — {reason}", style="Card.Error.TLabel",
                      wraplength=px(440), justify=tk.LEFT).pack(side=tk.LEFT)
            if self.hotkeys is not None:
                ttk.Button(self.hk_row, text="Try again", style="Link.TButton",
                           command=lambda: self.refresh_hotkeys(reason="manual")).pack(side=tk.RIGHT)

    def _build_menu(self):
        m = tk.Menu(self.root)
        acc = "Cmd" if IS_MAC else "Ctrl"
        fm = tk.Menu(m, tearoff=False)
        fm.add_command(label="Open App Folder", command=self.open_app_folder)
        fm.add_separator()
        fm.add_command(label="Hide Window", command=self.hide_window)
        if not IS_MAC:  # macOS provides Quit in the application menu (Cmd+Q)
            fm.add_command(label="Quit VTTA", accelerator=f"{acc}+Q", command=self.quit_app)
        m.add_cascade(label="File", menu=fm)
        tm = tk.Menu(m, tearoff=False)
        tm.add_command(label="History…", accelerator=f"{acc}+H", command=self.show_history)
        tm.add_command(label="Settings…", accelerator=f"{acc}+,", command=self.open_settings)
        tm.add_separator()
        tm.add_command(label="Reload Hotkeys", command=lambda: self.refresh_hotkeys(reason="manual"))
        tm.add_command(label="Diagnostics…", command=self.show_diagnostics)
        m.add_cascade(label="Tools", menu=tm)
        hm = tk.Menu(m, tearoff=False)
        hm.add_command(label="Shortcuts & Tips", command=self.show_help)
        hm.add_command(label="About VTTA", command=self.show_about)
        m.add_cascade(label="Help", menu=hm)
        self.root.config(menu=m)
        if IS_MAC:
            try:
                self.root.createcommand("tk::mac::ShowPreferences", self.open_settings)
                self.root.createcommand("::tk::mac::ReopenApplication", self.show_window)  # Dock icon click
                self.root.createcommand("::tk::mac::Quit", self.quit_app)
                self.root.createcommand("tk::mac::ShowHelp", self.show_help)
            except Exception:
                pass

    def _bind_shortcuts(self):
        self.root.bind(f"<{MOD_KEY}-q>", lambda e: self.quit_app())
        self.root.bind(f"<{MOD_KEY}-h>", lambda e: self.show_history())
        self.root.bind(f"<{MOD_KEY}-comma>", lambda e: self.open_settings())

    # ---- UI state ----------------------------------------------------------

    def _refresh_state(self):
        """Derive the one visible app state from recorder / job / model state."""
        rec = self._rec
        n = len(self._pending_jobs)
        if rec:
            state, text = "recording", {"search": "Recording search", "dictate": "Dictating"}.get(rec.mode, "Recording")
        elif n:
            state, text = "busy", "Transcribing" + (f" · {n - 1} queued" if n > 1 else "…")
        elif self._model_state in ("loading", "downloading", "copying"):
            state, text = "loading", "Loading model…"
        elif self._model_state == "error":
            state, text = "error", "Model unavailable"
        elif self._error_flag:
            state, text = "error", "Needs attention"
        else:
            state, text = "ready", "Ready"
        color = {"recording": C["recording"], "busy": C["busy"], "loading": C["loading"],
                 "error": C["error"], "ready": C["ready"]}[state]
        self.state_dot.itemconfigure(self._state_dot_id, fill=color)
        self.state_label.configure(text=text)

        if rec:
            self.start_button.configure(text="■  Stop & Transcribe", style="Stop.TButton")
        else:
            self.start_button.configure(text="●  Start Dictation", style="Primary.TButton")
        self.start_button.state(["!disabled"] if pyaudio else ["disabled"])
        if rec or n:
            self.discard_button.configure(text="Discard" if rec else "Cancel")
            if not self.discard_button.winfo_ismapped():
                self.discard_button.pack(side=tk.LEFT, padx=(self.px(6), 0), after=self.start_button)
        elif self.discard_button.winfo_ismapped():
            self.discard_button.pack_forget()
        self.meter_hint.configure(text="Live while recording" if not rec else
                                  ("Hearing you" if rec.heard_speech else "Listening…"))

        if n and not rec:
            if not self.busy_bar.winfo_ismapped():
                self.busy_bar.pack(fill=tk.X, pady=(self.px(12), 0), after=self._action_row)
                self.busy_bar.start(15)
        elif self.busy_bar.winfo_ismapped():
            self.busy_bar.stop()
            self.busy_bar.pack_forget()

        if self._failed_job and not rec and not n:
            if not self.retry_btn.winfo_ismapped():
                self.retry_btn.pack(side=tk.RIGHT, padx=self.px(6))
        elif self.retry_btn.winfo_ismapped():
            self.retry_btn.pack_forget()

        self._update_model_status()
        self._update_tray(state)

    def _update_model_status(self):
        name = self._wanted_model or self.config["whisper_model"]
        st = self._model_state
        show_bar = False
        if st == "downloading":
            done, total = self._model_progress or (0, 0)
            mb = f"{done / 1e6:,.0f} of {total / 1e6:,.0f} MB" if total else f"{done / 1e6:,.0f} MB"
            txt = f"Downloading the “{name}” model into the app folder — {mb} (one time only)"
            if total:
                show_bar = True
                self.model_bar.configure(value=done / total)
        elif st == "copying":
            txt = f"Copying the “{name}” model into the app folder…"
        elif st == "loading":
            txt = f"Loading the “{name}” model… You can already record; transcription starts when it’s ready."
        elif st == "ready":
            txt = f"“{self._model_name}” model ready · transcription runs locally on this computer"
        elif st == "unloaded":
            txt = (f"“{name}” model unloaded to save memory · it reloads automatically "
                   "when you dictate (takes a moment)")
        else:
            txt = "No model loaded — pick a model to try again. Tools → Diagnostics has details."
        self.model_status.configure(text=txt)
        if show_bar and not self.model_bar.winfo_ismapped():
            self.model_bar.pack(side=tk.RIGHT)
        elif not show_bar and self.model_bar.winfo_ismapped():
            self.model_bar.pack_forget()

    def _update_hotkey_label(self):
        self._build_hotkey_row()

    def update_status(self, status):
        """Safely update the status bar from any thread."""
        if not self._on_main():
            self._post(self.update_status, status)
            return
        try:
            self.status_var.set(status)
            self.status_time.configure(text=datetime.now().strftime("%H:%M"))
            if self.details_btn.winfo_ismapped():
                self.details_btn.pack_forget()
        except Exception:
            pass

    def _fmt_num(self, v):
        return f"{float(v):g}"

    def _lang_display(self, code):
        for c, name in LANGUAGES:
            if c == code:
                return name if c == "auto" else f"{name} ({c})"
        return code

    def _lang_code(self, display):
        for c, name in LANGUAGES:
            if display in (name, f"{name} ({c})", c):
                return c
        return "en"

    def _model_choices(self):
        cur = self.config.get("whisper_model", "base")
        vals = list(getattr(self, "_available_models", None) or DEFAULT_MODELS)
        if cur not in vals:
            vals.append(cur)
        return vals

    # ---- Editor ------------------------------------------------------------

    def _editor_text(self):
        return self.text_editor.get("1.0", tk.END).strip()

    def _on_editor_modified(self, _e=None):
        self.text_editor.edit_modified(False)
        text = self._editor_text()
        n = len(text.split()) if text else 0
        self.word_count.configure(text=f"{n} word{'s' if n != 1 else ''}" if n else "")
        self.search_button.state(["!disabled"] if text else ["disabled"])
        for b in (self.copy_btn, self.clear_btn):
            b.state(["!disabled"] if text else ["disabled"])
        if text or self.text_editor.get("1.0", "end-1c"):
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=self.px(12), y=self.px(10))

    def _set_editor_text(self, text):
        self.text_editor.edit_separator()
        self.text_editor.delete("1.0", tk.END)
        self.text_editor.insert("1.0", text)
        self.text_editor.edit_separator()
        self._on_editor_modified()

    def clear_editor(self):
        self._set_editor_text("")
        self.update_status(f"Transcript cleared. Press {MOD_LABEL}+Z in the text box to undo.")

    def copy_editor(self):
        text = self._editor_text()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.flash_button(self.copy_btn, "Copied ✓")
            self.update_status("Transcript copied to the clipboard.")

    # ---- Level meter & timer -----------------------------------------------

    def _draw_meter(self, level):
        c = self.meter
        w, h = int(c.cget("width")), int(c.cget("height"))
        thr = self.config["silence_threshold_pct"] / 100.0
        frac = _level_to_frac(level) if level > 0 else 0.0
        c.coords(self._meter_bar, 0, 0, int(w * frac), h)
        c.itemconfigure(self._meter_bar, fill=C["meter_ok"] if level >= thr else C["meter_low"])
        tx = int(w * _level_to_frac(thr))
        c.coords(self._meter_tick, tx - 1, 0, tx + 1, h)

    def _tick_recording(self):
        """Runs 10x/second only while recording; stops itself otherwise."""
        self._tick_id = None
        rec = self._rec
        if not rec:
            self._draw_meter(0.0)
            self.timer_label.configure(text="")
            return
        elapsed = time.monotonic() - rec.started
        self.timer_label.configure(text=f"{self._fmt_time(elapsed)} / {self._fmt_time(rec.max_seconds)}")
        self._draw_meter(rec.level)
        self._overlay_update_recording(elapsed, rec.level)
        base = self.state_label.cget("text").split(" · ")[0]
        self.state_label.configure(text=f"{base} · {self._fmt_time(elapsed)}")
        hint = "Hearing you" if rec.heard_speech else "Listening…"
        if self.meter_hint.cget("text") != hint:
            self.meter_hint.configure(text=hint)
        self._tick_id = self.root.after(100, self._tick_recording)

    @staticmethod
    def _fmt_time(sec):
        sec = int(sec)
        return f"{sec // 60}:{sec % 60:02d}"

    # ======================================================================
    # Config & DB
    # ======================================================================

    def load_config(self):
        self.config = dict(DEFAULT_CONFIG)
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if not isinstance(loaded, dict):
                    raise ValueError("config.json does not contain an object")
                self.config.update(loaded)
            except Exception as e:
                self.handle_error("Settings could not be read",
                                  "config.json could not be read, so default settings are in use. "
                                  "Saving settings will replace it.", details=e, show_message=False)
        # Repair wrong types from hand edits so the rest of the app can trust them.
        for key, default in DEFAULT_CONFIG.items():
            val = self.config.get(key)
            if isinstance(default, bool):
                self.config[key] = bool(val) if isinstance(val, (bool, int)) else default
            elif isinstance(default, (int, float)):
                try:
                    num = float(val)
                    if not math.isfinite(num):
                        raise ValueError
                    self.config[key] = num if isinstance(default, float) or num != int(num) else int(num)
                except (TypeError, ValueError):
                    self.config[key] = default
            elif not isinstance(val, str):
                self.config[key] = default
        if self.config["language"] not in [c for c, _ in LANGUAGES]:
            LANGUAGES.append((self.config["language"], self.config["language"]))
        if self.config["overlay_position"] not in [k for k, _ in OVERLAY_POSITIONS]:
            self.config["overlay_position"] = "bottom-right"
        if not self.config_file.exists():
            self.save_config()

    def save_config(self):
        tmp = self.config_file.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4)
            os.replace(tmp, self.config_file)  # atomic: a crash never leaves a half-written config
        except Exception as e:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            self.handle_error("Settings not saved", f"Could not write {self.config_file.name}.", details=e)

    def _set_config(self, key, value):
        self.config[key] = value
        self.save_config()

    def init_database(self):
        try:
            with sqlite3.connect(self.history_db) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT,
                        text TEXT,
                        type TEXT
                    )
                ''')
        except Exception as e:
            self.handle_error("History unavailable", "The history database could not be opened.",
                              details=e, show_message=False)

    def _cleanup_stale_downloads(self):
        """Remove interrupted model downloads/copies (*.part) left by a crash. Only our folder, only old files."""
        try:
            for p in self.models_dir.glob("*.part"):
                if time.time() - p.stat().st_mtime > 6 * 3600:
                    p.unlink()
        except Exception:
            pass

    # ======================================================================
    # Whisper model worker (single thread: loads and transcriptions never race)
    # ======================================================================

    def request_model(self, name):
        self._wanted_model = name
        self._model_state = "loading"
        self._tasks.put(("load", name))

    def _model_worker(self):
        while not self._shutdown.is_set():
            task = self._tasks.get()
            if task is None or self._shutdown.is_set():
                return
            kind, arg = task
            if kind == "load":
                if arg != self._wanted_model:
                    continue  # superseded by a newer selection
                if arg == self._model_name and self._model is not None:
                    self._post(self._on_model_loaded, arg, None, None)  # already loaded
                    continue
                self._load_model(arg)
            elif kind == "unload":
                if self._model is not None and self._tasks.empty():
                    self._model = None
                    gc.collect()
                    self._post(self._on_model_unloaded)
            elif kind == "transcribe":
                job = arg
                if job.cancelled:
                    self._post(self._on_job_done, job, None, None)
                    continue
                if self._model is None and self._wanted_model:
                    self._load_model(self._wanted_model)   # reload after an idle unload
                if self._model is None:
                    self._post(self._on_job_done, job, None,
                               RuntimeError("No Whisper model is loaded. Choose a model, then retry."))
                    continue
                try:
                    audio = np.frombuffer(job.audio, dtype=np.int16).astype(np.float32) / 32768.0
                    dev = getattr(getattr(self._model, "device", None), "type", "cpu")
                    opts = {"fp16": dev == "cuda"}
                    if job.language and job.language != "auto":
                        opts["language"] = job.language
                    result = self._model.transcribe(audio, **opts)
                    self._post(self._on_job_done, job, (result.get("text") or "").strip(), None)
                except Exception as e:
                    self._post(self._on_job_done, job, None, e)

    def _load_model(self, name):
        self._post(self._on_model_progress, "loading", None)
        try:
            import whisper
            try:
                self._post(self._set_available_models, list(whisper.available_models()))
            except Exception:
                pass
        except Exception as e:
            self._post(self._on_model_loaded, name, None,
                       RuntimeError(f"Whisper is not installed or failed to import ({e}). "
                                    "Run: pip install -r requirements.txt"))
            return
        previous = self._model_name
        try:
            root = self._ensure_model_file(whisper, name)
            self._model = None  # release the old model before loading a new one
            gc.collect()
            model = whisper.load_model(name, download_root=str(root))
            self._model, self._model_name = model, name
            self._post(self._on_model_loaded, name, None, None)
        except Exception as e:
            fallback = previous or ("base" if name != "base" else None)
            loaded = None
            if fallback and not self._shutdown.is_set():
                try:
                    root = self._ensure_model_file(whisper, fallback)
                    self._model = whisper.load_model(fallback, download_root=str(root))
                    self._model_name = loaded = fallback
                except Exception:
                    self._model, self._model_name = None, None
            self._post(self._on_model_loaded, name, loaded, e)

    def _ensure_model_file(self, whisper, name):
        """Make sure the model file is in the app's own models folder and return that folder.
        A copy found in Whisper's shared cache (~/.cache/whisper) is copied in (no download);
        otherwise it is downloaded with real progress and checksum verification."""
        self.models_dir.mkdir(parents=True, exist_ok=True)
        url = getattr(whisper, "_MODELS", {}).get(name)
        if not url:
            return self.models_dir
        fname = os.path.basename(url)
        target = self.models_dir / fname
        if target.exists():
            return self.models_dir
        expected = url.split("/")[-2]
        part = self.models_dir / (fname + ".part")
        shared = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "whisper" / fname
        try:
            if shared.exists():
                self._post(self._on_model_progress, "copying", None)
                shutil.copyfile(shared, part)
            else:
                sha = hashlib.sha256()
                with urllib.request.urlopen(url, timeout=30) as src, open(part, "wb") as out:
                    total = int(src.headers.get("Content-Length") or 0)
                    done, last = 0, 0.0
                    while True:
                        if self._shutdown.is_set():
                            raise RuntimeError("Download cancelled because VTTA is closing.")
                        buf = src.read(1 << 20)
                        if not buf:
                            break
                        out.write(buf)
                        sha.update(buf)
                        done += len(buf)
                        if time.monotonic() - last > 0.25:
                            last = time.monotonic()
                            self._post(self._on_model_progress, "downloading", (done, total))
                if len(expected) == 64 and sha.hexdigest() != expected:
                    raise RuntimeError("The downloaded model was corrupted (checksum mismatch). Please try again.")
            os.replace(part, target)
        finally:
            try:
                part.unlink(missing_ok=True)
            except Exception:
                pass
        self._post(self._on_model_progress, "loading", None)
        return self.models_dir

    def _set_available_models(self, models):
        self._available_models = models
        self.model_combo.configure(values=self._model_choices())

    def _on_model_progress(self, state, progress):
        self._model_state = state
        self._model_progress = progress
        self._refresh_state()

    def _on_model_unloaded(self):
        if self._model_state == "ready":
            self._model_state = "unloaded"
            self._refresh_state()

    def _schedule_idle_unload(self):
        """One-shot timer (not a loop): frees the model's memory after the configured idle time."""
        if self._unload_after_id:
            self.root.after_cancel(self._unload_after_id)
            self._unload_after_id = None
        minutes = float(self.config.get("unload_model_minutes", 0))
        if minutes > 0 and not self._shutting_down:
            self._unload_after_id = self.root.after(int(minutes * 60000), self._idle_unload)

    def _idle_unload(self):
        self._unload_after_id = None
        if self._rec or self._pending_jobs:
            self._schedule_idle_unload()
            return
        self._tasks.put(("unload", None))

    def _on_model_loaded(self, wanted, fallback_loaded, error):
        if wanted != self._wanted_model:
            return  # a newer model was requested meanwhile; its own result will follow
        if error is None:
            first = self._model_state != "unloaded"
            self._model_state = "ready"
            if first and not self._rec and not self._pending_jobs:
                self.update_status(f"“{wanted}” model loaded. Ready to dictate.")
        else:
            if fallback_loaded:
                self._model_state = "ready"
                self._wanted_model = fallback_loaded
                self.config["whisper_model"] = fallback_loaded
                self.save_config()
                self.model_var.set(fallback_loaded)
                msg = (f"Couldn’t load the “{wanted}” model, so VTTA is still using "
                       f"“{fallback_loaded}”.")
            else:
                self._model_state = "error"
                msg = f"Couldn’t load the “{wanted}” model, and no other model is available."
            self.handle_error("Model not loaded", f"{msg}\n\n{self._model_error_hint(error)}", details=error)
        self._schedule_idle_unload()
        self._refresh_state()

    @staticmethod
    def _model_error_hint(error):
        s = str(error).lower()
        if any(k in s for k in ("urlopen", "getaddrinfo", "timed out", "connection", "http error", "ssl",
                                "tunnel", "forbidden")):
            return ("The model could not be downloaded. Check your internet connection and try again; "
                    "once downloaded, models work offline.")
        if "memory" in s or "alloc" in s:
            return "Your computer ran out of memory. Try a smaller model."
        return f"Details: {error}"

    # ======================================================================
    # Hotkeys
    # ======================================================================

    def _init_hotkeys(self):
        if keyboard:
            self.hotkeys = _KeyboardBackend()
        elif pynput_keyboard:
            self.hotkeys = _PynputBackend()
        else:
            mod = "pynput" if IS_MAC else "keyboard"
            self._hotkey_error = f"the ‘{mod}’ package is unavailable ({_IMPORT_ERRORS.get(mod, 'not installed')})"
            self._diag("Hotkeys not active", self._hotkey_error)
            self._build_hotkey_row()
            return
        self.setup_hotkeys()

    def _register_hotkeys(self, trans_key, search_key):
        """(Re)register both hotkeys. Callbacks run on the hook thread and only post to Tk."""
        if self.hotkeys is None:
            return False, self._hotkey_error or "no hotkey backend"
        try:
            self.hotkeys.register([(trans_key, self._kb_transcription_hotkey),
                                   (search_key, self._kb_search_hotkey)])
        except Exception as e:
            self.hotkeys.unregister()
            return False, str(e) or type(e).__name__
        if not self.hotkeys.trusted:
            return False, ("macOS hasn’t allowed keyboard access yet. In System Settings → Privacy & "
                           "Security, enable this app (Terminal or Python) under Accessibility and Input "
                           "Monitoring, then restart VTTA.")
        return True, None

    def setup_hotkeys(self):
        ok, err = self._register_hotkeys(self.config["transcription_hotkey"], self.config["search_hotkey"])
        self._hotkeys_ok, self._hotkey_error = ok, err
        self._update_hotkey_label()
        if not ok:  # shown persistently in the hotkey row; no popup or global error state
            self._diag("Hotkeys not active", f"Global hotkeys could not be registered: {err}")
        return ok

    def _kb_transcription_hotkey(self):
        self._post(self._on_transcription_hotkey)

    def _kb_search_hotkey(self):
        self._post(self._on_search_hotkey)

    def refresh_hotkeys(self, reason="auto"):
        """Safely unhook/re-register hotkeys. Skips while recording."""
        if self._rec or self.hotkeys is None or self._capturing_hotkey:
            return
        self.hotkeys.reset()
        if self.setup_hotkeys() and reason in ("manual", "tray"):
            self.update_status("Hotkeys reloaded.")

    def start_hotkey_watchdog(self):
        """Re-registers hotkeys after sleep/hibernate. The thread just sleeps (no CPU) and wakes once a
        minute to compare clocks - a gap means the computer was asleep."""
        if self.hotkeys is None:
            return

        def loop():
            refresh_mins = max(0, int(self.config.get("hotkey_self_refresh_minutes", 60)))
            wake_gap = max(30, int(self.config.get("wake_gap_seconds", 180)))
            last = time.monotonic()
            wall = time.time()
            next_refresh_at = last + refresh_mins * 60 if refresh_mins else None
            while not self._shutdown.wait(60):
                now, now_wall = time.monotonic(), time.time()
                # monotonic may or may not include sleep depending on OS; wall clock always does
                if max(now - last, now_wall - wall) > wake_gap + 60:
                    self._post(self.refresh_hotkeys, "resume/wake")
                if next_refresh_at and now >= next_refresh_at:
                    if not self._rec and not self._pending_jobs:
                        self._post(self.refresh_hotkeys, "periodic")
                    next_refresh_at = now + refresh_mins * 60
                last, wall = now, now_wall
        threading.Thread(target=loop, name="vtta-hotkey-watchdog", daemon=True).start()

    def _press_debounced(self) -> bool:
        """True if we should proceed; False if still inside a debounce window."""
        now = time.monotonic()
        if now < self.debounce_press_until or now < self.debounce_until:
            return False
        self.debounce_press_until = now + int(self.config.get("hotkey_press_debounce_ms", 250)) / 1000.0
        return True

    def _post_debounce(self):
        self.debounce_until = time.monotonic() + int(self.config.get("hotkey_post_debounce_ms", 150)) / 1000.0

    def _on_transcription_hotkey(self):
        if self._capturing_hotkey or not self._press_debounced():
            return
        self.toggle_dictation_recording()

    def _on_search_hotkey(self):
        if self._capturing_hotkey or not self._press_debounced():
            return
        self.toggle_search_recording()

    # ======================================================================
    # Recording
    # ======================================================================

    def toggle_dictation_recording(self):
        if self._rec:
            self.stop_recording(mode="dictate")   # the key that stops decides what happens next
        else:
            self.start_recording("dictate", bool(self.config["auto_stop_hotkey"]))

    def toggle_search_recording(self):
        if self._rec:
            self.stop_recording(mode="search")
        else:
            self.start_recording("search", bool(self.config["auto_stop_hotkey"]))

    def on_primary_button(self):
        if self._rec:
            if self._rec.mode == "dictate":
                self._rec.mode = "editor"  # pasting now would paste into VTTA itself
            self.stop_recording()
        else:
            self.start_recording("editor", True)

    def on_discard_button(self):
        if self._rec:
            self.stop_recording(discard=True)
        else:
            self.cancel_transcriptions()

    def start_recording(self, mode, auto_stop):
        if self._rec or self._shutting_down:
            return
        if not pyaudio:
            self.handle_error("Microphone unavailable",
                              "PyAudio is not installed, so VTTA can’t record. "
                              "Run: pip install -r requirements.txt", details=_IMPORT_ERRORS.get("pyaudio"))
            return
        cfg = self.config
        rec = _Recording(mode, auto_stop, float(cfg["silence_duration"]),
                         float(cfg["silence_threshold_pct"]) / 100.0, float(cfg["max_recording_seconds"]),
                         cfg.get("input_device", ""))
        self._rec = rec
        self._error_flag = False
        self._failed_job = None
        rec.thread = threading.Thread(target=self._record_worker, args=(rec,), name="vtta-recorder", daemon=True)
        rec.thread.start()
        if mode == "dictate":
            hint = f"{_pretty_hotkey(cfg['transcription_hotkey'])} to stop"
        elif mode == "search":
            hint = f"{_pretty_hotkey(cfg['search_hotkey'])} to stop"
        else:
            hint = "Stops when you pause"
        self.update_status({"dictate": f"Dictating… press {hint} and paste.",
                            "search": f"Recording a search… press {hint} and search.",
                            }.get(mode, "Recording… stops after "
                                  f"{self._fmt_num(rec.silence_seconds)} s of silence, or click Stop."))
        self._overlay_show_recording(hint)
        self._refresh_state()
        self._tick_recording()

    def stop_recording(self, mode=None, discard=False):
        rec = self._rec
        if not rec or rec.stop_event.is_set():
            if rec and mode:
                rec.mode = mode
            return
        if mode:
            rec.mode = mode
        rec.discard = rec.discard or discard
        rec.stop_event.set()
        self.sounds.play("stop")

    def _resolve_input_device(self, pa, name):
        """Find the chosen microphone by name each time (indexes change when devices are plugged in)."""
        if not name:
            return None, False
        try:
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0 and info.get("name") == name:
                    return i, False
        except Exception:
            pass
        return None, True

    def _record_worker(self, rec):
        pa = stream = None
        try:
            self.sounds.play("start", block=True)   # cue first, so it isn't captured in the recording
            pa = pyaudio.PyAudio()
            index, rec.device_missing = self._resolve_input_device(pa, rec.device)
            stream = pa.open(format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE, input=True,
                             frames_per_buffer=CHUNK_SIZE, input_device_index=index)
            per_sec = SAMPLE_RATE / CHUNK_SIZE
            silence_limit = rec.silence_seconds * per_sec
            no_speech_limit = max(8.0, rec.silence_seconds * 3) * per_sec
            silent = chunks = 0
            while not rec.stop_event.is_set() and not self._shutdown.is_set():
                if time.monotonic() - rec.started >= rec.max_seconds:
                    rec.end_reason = "limit"
                    break
                data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                rec.frames.append(data)
                chunks += 1
                samples = np.frombuffer(data, dtype=np.int16).astype(np.int32)
                rec.level = float(np.abs(samples).mean()) / 32768.0 if samples.size else 0.0
                if rec.level >= rec.threshold:
                    rec.heard_speech = True
                    silent = 0
                else:
                    silent += 1
                if rec.auto_stop:
                    if rec.heard_speech and silent > silence_limit:
                        rec.end_reason = "silence"
                        break
                    if not rec.heard_speech and chunks > no_speech_limit:
                        rec.end_reason = "no_speech"
                        break
        except Exception as e:
            rec.error = e
        finally:
            for closer in (lambda: stream.stop_stream(), lambda: stream.close(), lambda: pa.terminate()):
                try:
                    closer()
                except Exception:
                    pass
            rec.level = 0.0
            self._post(self._on_recording_finished, rec)

    def _on_recording_finished(self, rec):
        if self._rec is rec:
            self._rec = None
        if self._tick_id:
            self.root.after_cancel(self._tick_id)
            self._tick_id = None
        self._draw_meter(0.0)
        self.timer_label.configure(text="")
        self._post_debounce()
        if not rec.stop_event.is_set() and rec.error is None and not rec.discard:
            self.sounds.play("stop")    # auto-stop (silence / limit): the user didn't press anything
        try:
            if rec.error is not None:
                self._overlay_hide()
                self.handle_error("Microphone error",
                                  "VTTA couldn’t record from the microphone. Check that a microphone is "
                                  "connected and not in use exclusively by another app, and that VTTA (or "
                                  "Python/Terminal) may use it in your privacy settings. You can pick a different "
                                  "microphone in Settings → Recording.", details=rec.error)
                return
            if rec.discard:
                self._overlay_hide()
                self.update_status("Recording discarded.")
                return
            if rec.end_reason == "no_speech":
                self._overlay_show_state("done", "No speech heard", "Recording stopped", hide_ms=1800)
                self.update_status("No speech detected, so recording stopped. If your microphone is quiet, "
                                   "lower the silence threshold in Settings → Recording.")
                return
            audio = b"".join(rec.frames)
            if len(audio) < SAMPLE_RATE * 2 * 0.3:
                self._overlay_hide()
                self.update_status("Recording was too short to transcribe.")
                return
            job = _Job(audio, rec.mode, self.config["language"])
            self._enqueue_job(job)
            why = {"silence": f" (stopped after {self._fmt_num(rec.silence_seconds)} s of silence)",
                   "limit": f" (reached the {self._fmt_time(rec.max_seconds)} recording limit)"}.get(rec.end_reason, "")
            note = " Your chosen microphone wasn’t found, so the system default was used." \
                if rec.device_missing else ""
            self.update_status(f"Transcribing {job.seconds:.1f} s of audio{why}…{note}")
        finally:
            self._refresh_state()

    # ======================================================================
    # Transcription results
    # ======================================================================

    def _enqueue_job(self, job):
        if self._unload_after_id:
            self.root.after_cancel(self._unload_after_id)
            self._unload_after_id = None
        self._pending_jobs.append(job)
        self._tasks.put(("transcribe", job))
        if self._model_state in ("loading", "downloading", "copying", "unloaded"):
            self._overlay_show_state("busy", "Loading model…", "Transcribes as soon as it’s ready")
        else:
            self._overlay_show_state("busy", "Transcribing…", f"{job.seconds:.1f} s of audio")

    def retry_failed_job(self):
        job = self._failed_job
        if not job:
            return
        self._failed_job = None
        retry = _Job(job.audio, job.mode if job.mode != "dictate" else "editor", self.config["language"])
        self._enqueue_job(retry)
        self.update_status("Retrying transcription… (the result goes to the transcript box)")
        self._refresh_state()

    def cancel_transcriptions(self):
        for job in self._pending_jobs:
            job.cancelled = True
        self._pending_jobs.clear()
        self._overlay_hide()
        self.update_status("Transcription cancelled.")
        self._refresh_state()

    def _on_job_done(self, job, text, error):
        if job in self._pending_jobs:
            self._pending_jobs.remove(job)
        self._post_debounce()
        if not self._pending_jobs:
            self._schedule_idle_unload()
        try:
            if job.cancelled:
                return
            if error is not None:
                self._failed_job = job
                self._overlay_hide()
                self.handle_error("Transcription failed",
                                  "The recording couldn’t be transcribed. It’s kept in memory — "
                                  "use “Retry transcription” in the status bar.\n\n"
                                  + self._model_error_hint(error), details=error)
                return
            if not text:
                self._overlay_show_state("done", "No speech recognized", "Nothing was pasted", hide_ms=1800)
                self.update_status("Transcription finished, but no speech was recognized.")
                return
            self._set_editor_text(text)
            words = len(text.split())
            self.sounds.play("done")
            if job.mode == "search":
                self.save_to_history(text, "search")
                self.perform_search_from_text(text, record_history=False)
                self._overlay_show_state("done", "Searching the web", f"“{self._clip(text, 30)}”",
                                         hide_ms=1800)
                return
            self.save_to_history(text, "transcription")
            if job.mode == "dictate" and self.write_to_cursor_var.get():
                self.insert_text_at_cursor(text)
            else:
                self._overlay_show_state("done", "Transcribed", f"{words} word{'s' if words != 1 else ''} "
                                         "· in the VTTA window", hide_ms=1500)
                self.update_status(f"Transcribed {words} word{'s' if words != 1 else ''}.")
        finally:
            self._refresh_state()

    @staticmethod
    def _clip(text, n):
        return text if len(text) <= n else text[: n - 1] + "…"

    def insert_text_at_cursor(self, text):
        if not text:
            return
        if not (pyperclip and pyautogui):
            missing = ", ".join(m for m, mod in (("pyperclip", pyperclip), ("pyautogui", pyautogui)) if not mod)
            self.handle_error("Can’t paste", f"Pasting needs {missing}. The text is in the transcript box.",
                              details="; ".join(_IMPORT_ERRORS.get(m, "") for m in ("pyperclip", "pyautogui")),
                              show_message=False)
            return
        try:
            focused_here = self.root.focus_get() is not None
        except Exception:
            focused_here = False
        if focused_here:
            self._overlay_show_state("done", "Transcribed", "VTTA was active, so nothing was pasted", hide_ms=1800)
            self.update_status("VTTA was the active window, so the text was placed in the transcript only.")
            return
        try:
            pyperclip.copy(text)
        except Exception as e:
            self.handle_error("Can’t paste", "Couldn’t copy the text to the clipboard. "
                              "It’s in the transcript box.", details=e)
            return

        def press():
            try:
                pyautogui.hotkey("command" if IS_MAC else "ctrl", "v")
                words = len(text.split())
                self._overlay_show_state("done", "Pasted", f"{words} word{'s' if words != 1 else ''}", hide_ms=1200)
                self.update_status(f"Pasted {words} word{'s' if words != 1 else ''} into the active app.")
            except Exception as e:
                hint = (" On macOS, allow this app under System Settings → Privacy & Security → "
                        "Accessibility." if IS_MAC else "")
                self.handle_error("Can’t paste", f"The text is on your clipboard — press {MOD_LABEL}+V "
                                  f"to paste it yourself.{hint}", details=e)
        self.update_status("Pasting…")
        self.root.after(150, press)  # let the clipboard settle without blocking the UI

    # ======================================================================
    # Overlay (small corner indicator)
    # ======================================================================

    def _overlay_enabled(self):
        return self.config.get("overlay_position", "bottom-right") != "off"

    def _overlay_ensure(self):
        w, h = self.px(268), self.px(66)
        if self.overlay_win is None or not self.overlay_win.winfo_exists():
            self.overlay_win = tk.Toplevel(self.root)
            self.overlay_win.withdraw()
            self.overlay_win.overrideredirect(True)
            try:
                self.overlay_win.attributes("-alpha", 0.95)
            except Exception:
                pass
            self.overlay_win.configure(bg=C["ink"])
            self.overlay_canvas = tk.Canvas(self.overlay_win, width=w, height=h, bg=C["ink"],
                                            highlightthickness=1, highlightbackground=C["ink_3"], bd=0)
            self.overlay_canvas.pack(fill=tk.BOTH, expand=True)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        pos = self.config.get("overlay_position", "bottom-right")
        mx, my_bottom, my_top = self.px(16), self.px(64 if IS_WIN else 84), self.px(40 if IS_MAC else 16)
        x = mx if pos.endswith("left") else sw - w - mx
        y = my_top if pos.startswith("top") else sh - h - my_bottom
        self.overlay_win.geometry(f"{w}x{h}+{x}+{y}")

    def _overlay_render(self, level=None):
        cv = self.overlay_canvas
        if not cv:
            return
        px = self.px
        w, h = int(cv.cget("width")), int(cv.cget("height"))
        cv.delete("all")
        color = {"recording": C["coral"], "busy": "#F59F00", "done": "#51CF66", "error": "#FF6B6B"}.get(
            self.overlay_state, C["coral"])
        cv.create_rectangle(0, 0, px(4), h, fill=color, outline="")   # state-colored edge
        if self.overlay_state == "recording" and not self.overlay_blink_on:
            color = "#6B2A24"
        r = px(9)
        cx, cy = px(28), h // 2 - (px(3) if level is not None else 0)
        if self.overlay_state == "done":
            cv.create_line(cx - r * .6, cy, cx - r * .1, cy + r * .5, cx + r * .7, cy - r * .5,
                           fill=color, width=px(3), capstyle=tk.ROUND, joinstyle=tk.ROUND)
        elif self.overlay_state == "error":
            cv.create_text(cx, cy, text="!", fill=color, font=self.f_overlay_title)
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=px(2))
        elif self.overlay_state == "busy":
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#4A3A12", width=px(3))
            cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=self._spin_angle(), extent=110,
                          style=tk.ARC, outline=color, width=px(3))
        else:
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline=color)
        title, sub = self.overlay_text
        cv.create_text(px(50), cy - px(9), anchor="w", text=title, fill=C["ink_text"], font=self.f_overlay_title)
        cv.create_text(px(50), cy + px(10), anchor="w", text=sub, fill=C["ink_muted"], font=self.f_overlay_sub)
        if level is not None:
            bw = w - px(50) - px(16)
            y = h - px(10)
            cv.create_rectangle(px(50), y, px(50) + bw, y + px(3), fill="#343B4C", outline="")
            cv.create_rectangle(px(50), y, px(50) + int(bw * _level_to_frac(level)), y + px(3),
                                fill=C["coral"], outline="")

    def _spin_angle(self):
        return int(time.monotonic() * 360) % 360

    def _overlay_show_recording(self, hint):
        """Show the indicator on every start and briefly make it topmost, then drop back so it can be covered."""
        self._overlay_hint = hint
        if not self._overlay_enabled():
            return
        try:
            self._overlay_ensure()
            self._overlay_cancel_hide()
            self.overlay_state = "recording"
            title = {"dictate": "Dictating", "search": "Recording search"}.get(self._rec.mode, "Recording")
            self.overlay_text = (title, f"0:00 · {hint}")
            self.overlay_blink_on = True
            self._overlay_render(0.0)
            self.overlay_win.deiconify()
            self.overlay_win.lift()
            try:
                self.overlay_win.attributes("-topmost", True)
            except Exception:
                pass
            if self.overlay_top_flash_after_id:
                self.overlay_win.after_cancel(self.overlay_top_flash_after_id)
            self.overlay_top_flash_after_id = self.overlay_win.after(
                int(self.config.get("overlay_flash_top_ms", 800)), self._overlay_end_top_flash)
            if not self.overlay_blink_after_id:
                self._overlay_blink_tick()
        except Exception as e:
            self.handle_error("Overlay error", "The recording indicator couldn’t be shown.", details=e,
                              show_message=False)

    def _overlay_update_recording(self, elapsed, level):
        if self.overlay_state != "recording" or not self._rec:
            return
        title = {"dictate": "Dictating", "search": "Recording search"}.get(self._rec.mode, "Recording")
        self.overlay_text = (title, f"{self._fmt_time(elapsed)} · {self._overlay_hint}")
        self._overlay_render(level)

    def _overlay_show_state(self, state, title, sub, hide_ms=None):
        """Transcribing / done / error feedback, so hotkey users get a result without switching windows."""
        if not self._overlay_enabled():
            return
        try:
            self._overlay_ensure()
            self._overlay_stop_blink()
            self._overlay_cancel_hide()
            was_visible = self.overlay_win.winfo_viewable()
            self.overlay_state = state
            self.overlay_text = (title, sub)
            self._overlay_render()
            if not was_visible:
                self.overlay_win.deiconify()
            if state == "busy":
                self._overlay_spin()
            if hide_ms:
                self.overlay_hide_after_id = self.overlay_win.after(hide_ms, self._overlay_hide)
        except Exception:
            pass

    def _overlay_spin(self):
        """Animate the busy spinner only while the overlay shows 'busy'."""
        self.overlay_blink_after_id = None
        if self.overlay_state != "busy" or not (self.overlay_win and self.overlay_win.winfo_exists()):
            return
        self._overlay_render()
        self.overlay_blink_after_id = self.overlay_win.after(60, self._overlay_spin)

    def _overlay_end_top_flash(self):
        self.overlay_top_flash_after_id = None
        if self.overlay_win and self.overlay_win.winfo_exists():
            try:
                self.overlay_win.attributes("-topmost", False)
            except Exception:
                pass

    def _overlay_stop_blink(self):
        if self.overlay_blink_after_id and self.overlay_win:
            try:
                self.overlay_win.after_cancel(self.overlay_blink_after_id)
            except Exception:
                pass
        self.overlay_blink_after_id = None

    def _overlay_cancel_hide(self):
        if self.overlay_hide_after_id and self.overlay_win:
            try:
                self.overlay_win.after_cancel(self.overlay_hide_after_id)
            except Exception:
                pass
        self.overlay_hide_after_id = None

    def _overlay_hide(self):
        if not self._on_main():
            self._post(self._overlay_hide)
            return
        try:
            self._overlay_stop_blink()
            self._overlay_cancel_hide()
            if self.overlay_top_flash_after_id and self.overlay_win:
                self.overlay_win.after_cancel(self.overlay_top_flash_after_id)
            self.overlay_top_flash_after_id = None
            if self.overlay_win and self.overlay_win.winfo_exists():
                try:
                    self.overlay_win.attributes("-topmost", False)
                except Exception:
                    pass
                self.overlay_win.withdraw()
            self.overlay_state = None
        except Exception:
            pass

    def _overlay_blink_tick(self):
        self.overlay_blink_after_id = None
        if self.overlay_state != "recording" or not (self.overlay_win and self.overlay_win.winfo_exists()):
            return
        self.overlay_blink_on = not self.overlay_blink_on
        self._overlay_render(self._rec.level if self._rec else 0.0)
        interval = max(100, int(self.config.get("overlay_blink_ms", 500)))
        self.overlay_blink_after_id = self.overlay_win.after(interval, self._overlay_blink_tick)

    # ======================================================================
    # Web search
    # ======================================================================

    def perform_search_from_button(self):
        text = self._editor_text()
        if not text:
            self.update_status("Type or dictate some text first, then search for it.")
            return
        self.perform_search_from_text(text)

    def perform_search_from_text(self, text, record_history=True):
        if record_history:
            self.save_to_history(text, "search")
        url = SEARCH_URL.format(quote_plus(text))
        if self.config.get("search_opener") == "default":
            self._open_default_browser(url)
            return
        self.update_status("Opening a private browser window…")
        t = threading.Thread(target=self.web_search, args=(url,), name="vtta-browser", daemon=True)
        self._browser_threads.add(t)
        t.start()

    def _open_default_browser(self, url):
        try:
            webbrowser.open(url, new=2)
            self.update_status("Search opened in your default browser.")
        except Exception as e:
            self.handle_error("Search failed", "Your default browser couldn’t be opened.", details=e)

    def _offer_default_browser(self, url, error):
        self._diag("Private browser window failed", str(error), error)
        first = (str(error).splitlines() or [type(error).__name__])[0]
        msg = ("The private browser window couldn’t be opened.\n\n"
               f"{self._clip(first, 200)}\n\n"
               "You can install its browser into the app folder from Settings → Web Search.\n\n"
               "Open this search in your default browser instead?")
        if messagebox.askyesno("Search", msg, parent=self.root if self._window_visible() else None):
            self._open_default_browser(url)
        else:
            self.update_status("Search not opened.")

    def _use_local_browsers(self):
        """Prefer the browser installed inside the app folder (scoped to this process and its children)."""
        if self.local_browser_installed():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(self.browsers_dir)

    def web_search(self, url):
        """Runs on its own thread. All Playwright calls stay on this thread (its sync API is thread-bound)."""
        try:
            self._use_local_browsers()
            try:
                from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
            except Exception as e:
                self._post(self._offer_default_browser, url, e)
                return
            with sync_playwright() as p:
                browser = None
                user_path = self.config.get("browser_path")
                try:
                    if user_path and os.path.exists(user_path):
                        try:
                            browser = p.chromium.launch(executable_path=user_path, headless=False)
                        except Exception as e:
                            self._diag("Custom browser failed", f"{user_path}: falling back to Playwright Chromium", e)
                    if not browser:
                        browser = p.chromium.launch(headless=False)
                except Exception as e:
                    self._post(self._offer_default_browser, url, e)
                    return
                try:
                    context = browser.new_context()
                    page = context.new_page()
                    try:
                        page.goto(url, wait_until="commit", timeout=15000)  # don't block the close/timeout loop
                        self._post(self.update_status, "Search results are open in the private browser window.")
                    except Exception as e:  # e.g. offline: the window stays open and shows the browser's error page
                        self._post(self.update_status, "The browser opened, but the results page didn’t load. "
                                                       "Check your internet connection.")
                        self._diag("Search page didn’t load", url, e)
                    timeout_min = float(self.config.get("browser_timeout_minutes", 60))
                    start = time.monotonic()
                    deadline = start + timeout_min * 60 if timeout_min > 0 else None
                    warn_at = deadline - 300 if deadline and timeout_min > 5 else None
                    while not self._shutdown.is_set() and browser.is_connected():
                        if not context.pages:
                            break  # the user closed the window/tabs
                        now = time.monotonic()
                        if deadline and now >= deadline:
                            self._post(self.update_status, "Browser auto-close time reached; closed the browser.")
                            break
                        if warn_at and now >= warn_at:
                            warn_at = None
                            self._post(self.update_status,
                                       "The private browser window will close automatically in 5 minutes.")
                        try:
                            context.wait_for_event("close", timeout=1000)  # pumps Playwright events
                            break
                        except PWTimeout:
                            continue
                finally:
                    try:
                        browser.close()
                    except Exception:
                        pass
            self._post(self.update_status, "Browser closed.")
        except Exception as e:
            if "closed" not in str(e).lower():
                self.handle_error("Search failed", "The browser session ended with an error.", details=e)
        finally:
            self._browser_threads.discard(threading.current_thread())

    def local_browser_installed(self):
        try:
            return self.browsers_dir.is_dir() and any(self.browsers_dir.glob("chromium*"))
        except Exception:
            return False

    def install_private_browser(self, on_update):
        """Install Playwright's Chromium into the app folder. on_update(state, text) is called on the Tk thread
        with state in {"running", "done", "failed"}. Returns the process (owned by the app until it exits)."""
        if getattr(sys, "frozen", False):
            on_update("failed", "Not available in the packaged app. Use “My default browser” instead.")
            return None
        env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(self.browsers_dir))
        try:
            proc = subprocess.Popen([sys.executable, "-m", "playwright", "install", "chromium"], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            on_update("failed", f"Couldn’t start the installer: {e}")
            return None
        self._child_procs.add(proc)

        def read():
            last = ""
            try:
                for line in proc.stdout:
                    line = line.strip().replace("■", "").strip()
                    if line:
                        last = line
                        self._post(on_update, "running", self._clip(line, 70))
            except Exception:
                pass
            code = proc.wait()
            self._child_procs.discard(proc)
            if self._shutting_down:
                return
            if code == 0:
                self._post(on_update, "done", "Installed in the app folder.")
            else:
                self._post(self._diag, "Browser install failed", f"exit code {code}: {last}")
                self._post(on_update, "failed", f"Install failed: {self._clip(last, 90) or 'exit code ' + str(code)}")
        threading.Thread(target=read, name="vtta-browser-install", daemon=True).start()
        return proc

    # ======================================================================
    # History
    # ======================================================================

    def save_to_history(self, text, entry_type):
        if not self.record_history_var.get():
            return
        try:
            with sqlite3.connect(self.history_db) as conn:
                conn.execute("INSERT INTO history (timestamp, text, type) VALUES (?, ?, ?)",
                             (datetime.now().isoformat(), text, entry_type))
            if self._history_win and self._history_win.winfo_exists():
                self._history_reload()
        except Exception as e:
            self.handle_error("History not saved", "The entry couldn’t be written to history.db.",
                              details=e, show_message=False)

    def show_history(self):
        if self._history_win and self._history_win.winfo_exists():
            self._history_win.deiconify()
            self._history_win.lift()
            self._history_search.focus_set()
            return
        px = self.px
        win = tk.Toplevel(self.root)
        self._history_win = win
        win.title("History — VTTA")
        win.configure(bg=C["bg"])
        win.minsize(px(560), px(340))
        self.center_window(win, px(800), px(540))
        win.bind("<Escape>", lambda e: win.destroy())

        top = ttk.Frame(win, padding=(px(16), px(14), px(16), px(8)))
        top.pack(fill=tk.X)
        ttk.Label(top, text="History", style="Section.TLabel").pack(side=tk.LEFT)
        self._history_query = tk.StringVar()
        self._history_search = ttk.Entry(top, textvariable=self._history_query, width=30)
        self._history_search.pack(side=tk.LEFT, padx=(px(16), px(10)))
        Tooltip(self._history_search, "Type to filter entries")
        self._history_count = ttk.Label(top, text="", style="Muted.TLabel")
        self._history_count.pack(side=tk.LEFT)
        if not self.record_history_var.get():
            ttk.Label(win, text="Saving is off — turn on “Save to history” in the main window to keep "
                                "new transcripts.", style="Muted.TLabel", padding=(px(16), 0, px(16), px(6))).pack(fill=tk.X)

        mid = tk.Frame(win, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
        mid.pack(fill=tk.BOTH, expand=True, padx=px(16))
        cols = ("time", "type", "text")
        tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        tree.heading("time", text="When", anchor=tk.W)
        tree.heading("type", text="Type", anchor=tk.W)
        tree.heading("text", text="Text", anchor=tk.W)
        tree.column("time", width=px(140), stretch=False)
        tree.column("type", width=px(100), stretch=False)
        tree.column("text", width=px(480))
        tree.tag_configure("odd", background=C["subtle"])
        vsb = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=tree.yview, style="Slim.Vertical.TScrollbar")
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, px(2)), pady=px(2))
        tree.pack(fill=tk.BOTH, expand=True)
        self._history_tree = tree
        self._history_rows = {}
        self._history_empty = tk.Label(mid, text="", bg=C["surface"], fg=C["faint"], font=self.f_body)

        bot = ttk.Frame(win, padding=(px(16), px(12)))
        bot.pack(fill=tk.X)
        b_use = ttk.Button(bot, text="Use in Transcript", style="Small.TButton", command=self._history_use)
        b_use.pack(side=tk.LEFT)
        Tooltip(b_use, "Put the selected entry in the transcript box (or double-click it)")
        b_copy = ttk.Button(bot, text="Copy", style="Small.TButton", command=self._history_copy)
        b_copy.pack(side=tk.LEFT, padx=(px(8), 0))
        self._history_copy_btn = b_copy
        b_del = ttk.Button(bot, text="Delete Selected", style="Danger.TButton", command=self._history_delete)
        b_del.pack(side=tk.LEFT, padx=(px(8), 0))
        ttk.Button(bot, text="Clear All…", style="Danger.TButton", command=self.clear_history).pack(side=tk.RIGHT)
        self._history_sel_buttons = (b_use, b_copy, b_del)

        def on_select(_e=None):
            sel = tree.selection()
            for b in self._history_sel_buttons:
                b.state(["!disabled"] if sel else ["disabled"])
        tree.bind("<<TreeviewSelect>>", on_select)
        tree.bind("<Double-1>", lambda e: self._history_use())
        tree.bind("<Return>", lambda e: self._history_use())
        tree.bind("<Delete>", lambda e: self._history_delete())
        tree.bind(f"<{MOD_KEY}-c>", lambda e: self._history_copy())
        self._history_after = None

        def on_query(*_):
            if self._history_after:
                win.after_cancel(self._history_after)
            self._history_after = win.after(200, self._history_reload)
        self._history_query.trace_add("write", on_query)
        self._history_reload()
        on_select()
        self._history_search.focus_set()

    def _history_reload(self):
        tree = self._history_tree
        q = self._history_query.get().strip()
        tree.delete(*tree.get_children())
        self._history_rows = {}
        try:
            with sqlite3.connect(self.history_db) as conn:
                total = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
                sql = "SELECT id, timestamp, type, text FROM history"
                args = ()
                if q:
                    sql += " WHERE text LIKE ? ESCAPE '\\'"
                    args = ("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",)
                rows = conn.execute(sql + " ORDER BY id DESC LIMIT 2000", args).fetchall()
        except Exception as e:
            self.handle_error("History unavailable", "history.db couldn’t be read.", details=e)
            return
        for n, (rid, ts, typ, text) in enumerate(rows):
            try:
                when = datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
            except Exception:
                when = ts or ""
            one_line = " ".join((text or "").split())
            tree.insert("", "end", iid=str(rid), values=(when, (typ or "").capitalize(), one_line),
                        tags=("odd",) if n % 2 else ())
            self._history_rows[str(rid)] = text or ""
        shown = len(rows)
        if q:
            self._history_count.configure(text=f"{shown} of {total} match")
        else:
            self._history_count.configure(text=f"{total} entr{'y' if total == 1 else 'ies'}"
                                          + (f" (newest {shown} shown)" if shown < total else ""))
        if not rows:
            self._history_empty.configure(text="No matches." if q else "No history yet.")
            self._history_empty.place(relx=0.5, rely=0.45, anchor="center")
        else:
            self._history_empty.place_forget()

    def _history_selected_texts(self):
        return [self._history_rows.get(i, "") for i in self._history_tree.selection()]

    def _history_use(self):
        texts = self._history_selected_texts()
        if texts:
            self._set_editor_text("\n\n".join(texts))
            self.update_status(f"History entry placed in the transcript box ({MOD_LABEL}+Z undoes).")

    def _history_copy(self):
        texts = self._history_selected_texts()
        if texts:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n\n".join(texts))
            self.flash_button(self._history_copy_btn, "Copied ✓")
            self.update_status(f"Copied {len(texts)} history entr{'y' if len(texts) == 1 else 'ies'}.")

    def _history_delete(self):
        ids = self._history_tree.selection()
        if not ids:
            return
        n = len(ids)
        if not messagebox.askyesno("Delete history",
                                   f"Permanently delete {n} selected entr{'y' if n == 1 else 'ies'}?",
                                   icon="warning", parent=self._history_win):
            return
        try:
            with sqlite3.connect(self.history_db) as conn:
                conn.executemany("DELETE FROM history WHERE id = ?", [(int(i),) for i in ids])
            self._history_reload()
            self.update_status(f"Deleted {n} history entr{'y' if n == 1 else 'ies'}.")
        except Exception as e:
            self.handle_error("Delete failed", "The entries couldn’t be deleted.", details=e)

    def clear_history(self):
        parent = self._history_win if self._history_win and self._history_win.winfo_exists() else self.root
        if not messagebox.askyesno("Clear history", "Permanently delete ALL history entries?\n\nThis can’t be undone.",
                                   icon="warning", parent=parent):
            return
        try:
            with sqlite3.connect(self.history_db) as conn:
                conn.execute("DELETE FROM history")
            try:
                with sqlite3.connect(self.history_db) as conn:
                    conn.execute("VACUUM")  # actually free the space on disk
            except Exception:
                pass
            self.update_status("History cleared.")
            if self._history_win and self._history_win.winfo_exists():
                self._history_reload()
        except Exception as e:
            self.handle_error("Clear failed", "History couldn’t be cleared.", details=e)

    # ======================================================================
    # Settings
    # ======================================================================

    def change_model(self, event=None):
        name = self.model_var.get()
        if name == self.config["whisper_model"] and self._model_state not in ("error",):
            return
        self.config["whisper_model"] = name
        self.save_config()
        self.request_model(name)
        self.update_status(f"Switching to the “{name}” model…")
        self._refresh_state()

    def change_language(self, event=None):
        code = self._lang_code(self.lang_var.get())
        self._set_config("language", code)
        self.update_status(f"Language set to {self._lang_display(code)} — applies from the next recording.")

    def open_settings(self):
        if self._settings_win and self._settings_win.winfo_exists():
            self._settings_win.deiconify()
            self._settings_win.lift()
            return
        SettingsDialog(self)

    def list_input_devices(self):
        """Enumerate microphones. Called only when the Settings window asks (no background scanning)."""
        if not pyaudio:
            return []
        names = []
        pa = None
        try:
            pa = pyaudio.PyAudio()
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0:
                    n = info.get("name", f"Device {i}")
                    if n not in names:
                        names.append(n)
        except Exception as e:
            self._diag("Microphone list failed", "", e)
        finally:
            if pa:
                try:
                    pa.terminate()
                except Exception:
                    pass
        return names

    def apply_settings(self, new):
        """Apply validated settings. Returns an error string for the dialog to show, or None."""
        old = dict(self.config)
        hk_changed = (new["transcription_hotkey"] != old["transcription_hotkey"]
                      or new["search_hotkey"] != old["search_hotkey"])
        if hk_changed and self.hotkeys is not None:
            ok, err = self._register_hotkeys(new["transcription_hotkey"], new["search_hotkey"])
            if not ok and self._hotkeys_ok:
                self._register_hotkeys(old["transcription_hotkey"], old["search_hotkey"])  # keep the working ones
                return f"Couldn’t register this hotkey: {err}"
            # If hotkeys weren't working before either, save anyway; the hotkey row shows the problem.
            self._hotkeys_ok, self._hotkey_error = ok, err
        startup = new.pop("_launch_at_startup", None)
        self.config.update(new)
        self.save_config()
        self._update_hotkey_label()
        self._draw_meter(self._rec.level if self._rec else 0.0)
        if self.overlay_win is not None and self.overlay_win.winfo_exists() and self.overlay_state:
            if self._overlay_enabled():
                self._overlay_ensure()
            else:
                self._overlay_hide()
        if new.get("enable_voice_commands"):
            self._voice_wake.set()
        if new.get("unload_model_minutes") != old.get("unload_model_minutes"):
            self._schedule_idle_unload()
        if startup is not None and startup != self.startup_entry_exists():
            (self.add_to_startup if startup else self.remove_from_startup)()
        elif startup and new.get("start_minimized") != old.get("start_minimized"):
            self.add_to_startup(quiet=True)
        self.update_status("Settings saved.")
        return None

    # ======================================================================
    # Diagnostics (in memory only; nothing is written to disk)
    # ======================================================================

    def _diag(self, context, message, details=None):
        if not self._on_main():
            self._post(self._diag, context, message, details)
            return
        entry = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {context}: {message}"
        if isinstance(details, BaseException):
            tb = "".join(traceback.format_exception(type(details), details, details.__traceback__)).strip()
            entry += "\n    " + tb.replace("\n", "\n    ")
        elif details:
            entry += f"\n    {details}"
        self._diagnostics.append(entry)
        try:
            print(entry, file=sys.stderr)
        except Exception:
            pass
        if self._diag_win and self._diag_win.winfo_exists():
            self._diag_reload()

    def show_diagnostics(self):
        if self._diag_win and self._diag_win.winfo_exists():
            self._diag_win.deiconify()
            self._diag_win.lift()
            return
        px = self.px
        win = tk.Toplevel(self.root)
        self._diag_win = win
        win.title("Diagnostics — VTTA")
        win.configure(bg=C["bg"])
        self.center_window(win, px(740), px(440))
        win.bind("<Escape>", lambda e: win.destroy())
        top = ttk.Frame(win, padding=(px(16), px(14), px(16), px(8)))
        top.pack(fill=tk.X)
        ttk.Label(top, text="Diagnostics", style="Section.TLabel").pack(side=tk.LEFT)
        ttk.Label(top, text="Problems from this session · kept in memory only, cleared when VTTA closes",
                  style="Muted.TLabel").pack(side=tk.LEFT, padx=(px(12), 0))
        frame = tk.Frame(win, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
        frame.pack(fill=tk.BOTH, expand=True, padx=px(16))
        self._diag_text = tk.Text(frame, wrap=tk.NONE, font=self.f_mono, relief="flat", bg=C["surface"],
                                  fg=C["text"], padx=px(10), pady=px(8), highlightthickness=0)
        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self._diag_text.yview, style="Slim.Vertical.TScrollbar")
        self._diag_text.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, px(2)), pady=px(2))
        self._diag_text.pack(fill=tk.BOTH, expand=True)
        bot = ttk.Frame(win, padding=(px(16), px(12)))
        bot.pack(fill=tk.X)

        def copy_all():
            self.root.clipboard_clear()
            self.root.clipboard_append(self._diag_text.get("1.0", tk.END))
            self.flash_button(copy_btn, "Copied ✓")

        def clear():
            self._diagnostics.clear()
            self._diag_reload()
        copy_btn = ttk.Button(bot, text="Copy All", style="Small.TButton", command=copy_all)
        copy_btn.pack(side=tk.LEFT)
        ttk.Button(bot, text="Clear", style="Small.TButton", command=clear).pack(side=tk.LEFT, padx=(px(8), 0))
        ttk.Button(bot, text="Close", style="Small.TButton", command=win.destroy).pack(side=tk.RIGHT)
        env = [f"VTTA {APP_VERSION} · Python {sys.version.split()[0]} · {sys.platform}",
               f"App folder: {self.script_dir}",
               f"Hotkeys: {self.hotkeys.name if self.hotkeys else 'unavailable'}"]
        for mod, err in _IMPORT_ERRORS.items():
            env.append(f"Module '{mod}' unavailable: {err}")
        self._diag_env = "\n".join(env)
        self._diag_reload()

    def _diag_reload(self):
        t = self._diag_text
        t.configure(state=tk.NORMAL)
        t.delete("1.0", tk.END)
        t.insert(tk.END, self._diag_env + "\n\n")
        t.insert(tk.END, "\n\n".join(self._diagnostics) if self._diagnostics else "No problems recorded.")
        t.configure(state=tk.DISABLED)
        t.see(tk.END)

    def handle_error(self, context, message, show_message=True, details=None):
        """Record a problem and tell the user. Safe to call from any thread."""
        if not self._on_main():
            self._post(self.handle_error, context, message, show_message, details)
            return
        if not hasattr(self, "_diagnostics"):
            print(f"[ERROR] {context}: {message} {details or ''}", file=sys.stderr)
            return
        self._diag(context, message.replace("\n\n", " "), details)
        if self._shutting_down or not hasattr(self, "status_var"):
            return
        first_line = message.split("\n")[0]
        self.update_status(f"{context}: {first_line}")
        self._error_flag = True
        self.details_btn.pack(side=tk.RIGHT, padx=self.px(6))
        self._refresh_state()
        self._post_debounce()
        if show_message:
            self.sounds.play("error")
        if self._window_visible():
            if show_message:
                messagebox.showerror(context, message, parent=self.root)
        elif show_message:
            # Don't steal focus from the app the user is typing in: use the overlay instead.
            self._overlay_show_state("error", context, self._clip(first_line, 38), hide_ms=4500)

    def _window_visible(self):
        try:
            return self.root.state() == "normal" and bool(self.root.winfo_viewable())
        except Exception:
            return False

    # ======================================================================
    # Help
    # ======================================================================

    def show_help(self):
        d = _pretty_hotkey(self.config["transcription_hotkey"])
        s = _pretty_hotkey(self.config["search_hotkey"])
        messagebox.showinfo("Shortcuts & Tips", (
            f"{d}  —  start dictating; press again to stop. The text is pasted into the app you were "
            "typing in (if “Paste into the active app” is on) and shown in VTTA.\n\n"
            f"{s}  —  start recording a search; press again to stop and open the results.\n"
            "Pressing the other hotkey to stop switches between paste and search.\n\n"
            "Start Dictation (in the window) records until you pause, then puts the text in the transcript box.\n\n"
            f"In VTTA:  {MOD_LABEL}+H history · {MOD_LABEL}+, settings · {MOD_LABEL}+Q quit · "
            f"{MOD_LABEL}+Z undo in the transcript.\n\n"
            "The level meter’s tick is the silence threshold. If recording stops too early or never stops, "
            "adjust it in Settings → Recording."), parent=self.root)

    def show_about(self):
        messagebox.showinfo("About VTTA", (
            f"VTTA — Voice to Text Assistant {APP_VERSION}\n\n"
            "Speech is transcribed locally with OpenAI Whisper. Audio is kept in memory only and never saved "
            "to disk. Optional voice commands use Google’s online recognizer.\n\n"
            f"App folder: {self.script_dir}"), parent=self.root)

    # ======================================================================
    # Tray / Dock & window lifecycle
    # ======================================================================

    def _can_hide(self):
        return IS_MAC or self._tray_ok

    def _tray_image(self, state):
        if state not in self._tray_images:
            img = self._icon_base.resize((64, 64), Image.LANCZOS).copy()
            color = {"recording": (255, 92, 77), "busy": (245, 159, 0), "error": (230, 70, 70)}.get(state)
            if color:
                d = ImageDraw.Draw(img)
                d.ellipse([38, 38, 63, 63], fill=(20, 23, 32, 255))
                d.ellipse([42, 42, 59, 59], fill=color + (255,))
            self._tray_images[state] = img
        return self._tray_images[state]

    def _start_tray(self):
        if IS_MAC:
            return  # the Dock icon plays this role on macOS
        if not pystray:
            self._diag("System tray unavailable", _IMPORT_ERRORS.get("pystray", "pystray missing"))
            return

        def setup(icon):
            icon.visible = True
            self._tray_ok = True

        def run():
            try:
                menu = pystray.Menu(
                    pystray.MenuItem("Show VTTA", lambda i, it: self._post(self.show_window), default=True),
                    pystray.MenuItem(lambda it: "Stop dictation" if self._rec else "Start dictation",
                                     lambda i, it: self._post(self.toggle_dictation_recording)),
                    pystray.MenuItem("Reload hotkeys", lambda i, it: self._post(self.refresh_hotkeys, "tray")),
                    pystray.MenuItem("Settings…", lambda i, it: self._post(self._show_and_settings)),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Quit VTTA", lambda i, it: self._post(self.quit_app)),
                )
                self._tray_icon = pystray.Icon(APP_NAME, self._tray_image("ready"), "VTTA", menu)
                self._tray_icon.run(setup=setup)
            except Exception as e:
                self._tray_ok = False
                self._diag("System tray unavailable", "Closing the window will quit VTTA.", e)
        threading.Thread(target=run, name="vtta-tray", daemon=True).start()

    def _update_tray(self, state):
        if self._tray_icon is None or not self._tray_ok or state == self._tray_state:
            return
        self._tray_state = state
        try:
            self._tray_icon.icon = self._tray_image(state)
            self._tray_icon.title = "VTTA — " + self.state_label.cget("text").split(" · ")[0]
            self._tray_icon.update_menu()
        except Exception:
            pass

    def _show_and_settings(self):
        self.show_window()
        self.open_settings()

    def hide_window(self):
        # On Linux pystray can't tell whether the icon really docked, so minimize there rather than hide.
        if self._can_hide() and (IS_WIN or IS_MAC):
            self.root.withdraw()
        else:
            self.root.iconify()

    def show_window(self, icon=None, item=None):
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except Exception:
            pass

    def on_close_window(self):
        if self.config.get("close_to_tray", True) and self._can_hide():
            if not self.config.get("tray_hint_shown"):
                where = "in the Dock (click its icon to reopen)" if IS_MAC else "in the system tray"
                quit_how = "Cmd+Q" if IS_MAC else "File → Quit or Quit VTTA in the tray menu"
                messagebox.showinfo("VTTA keeps running",
                                    f"VTTA is still running {where} so your hotkeys keep working.\n\n"
                                    f"To exit completely, use {quit_how}. You can make the close button quit "
                                    "in Settings → General.", parent=self.root)
                self._set_config("tray_hint_shown", True)
            self.hide_window()
        else:
            self.quit_app()

    def quit_app(self, icon=None, item=None):
        """Stop everything this app started, then close."""
        if not self._on_main():
            self._post(self.quit_app)
            return
        if self._shutting_down:
            return
        if self._rec or self._pending_jobs:
            what = "A recording is in progress" if self._rec else "A transcription is still running"
            if not messagebox.askyesno("Quit VTTA", f"{what}. Quit anyway and discard it?", icon="warning",
                                       parent=self.root if self._window_visible() else None):
                return
        self._shutting_down = True
        self._shutdown.set()
        self._voice_wake.set()
        if self._rec:
            self._rec.discard = True
            self._rec.stop_event.set()
        for job in self._pending_jobs:
            job.cancelled = True
        self._tasks.put(None)
        if self.hotkeys is not None:
            self.hotkeys.shutdown()
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        for proc in list(self._child_procs):     # only processes this app started
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        # Browser threads notice the shutdown flag within ~1 s and close their own browser.
        deadline = time.monotonic() + 4.0
        for t in list(self._browser_threads) + ([self._rec.thread] if self._rec and self._rec.thread else []):
            t.join(timeout=max(0.0, deadline - time.monotonic()))
        try:
            self.root.destroy()
        except Exception:
            pass

    # ======================================================================
    # Voice commands (optional, Google recognizer)
    # ======================================================================

    def start_voice_command_listener(self):
        """The listener thread only touches the microphone while voice commands are enabled;
        otherwise it sleeps on an event (no CPU, no microphone)."""
        def listen_for_commands():
            sr = recognizer = None
            while not self._shutdown.is_set():
                if not self.config.get("enable_voice_commands", False):
                    self._voice_wake.wait()      # sleeps until enabled in Settings or the app quits
                    self._voice_wake.clear()
                    continue
                if sr is None:
                    try:
                        import speech_recognition as sr  # noqa: F811
                        recognizer = sr.Recognizer()
                    except Exception as e:
                        self.handle_error("Voice commands unavailable",
                                          "The SpeechRecognition package is missing, so voice commands are off.",
                                          details=e, show_message=False)
                        self.config["enable_voice_commands"] = False
                        continue
                try:
                    with sr.Microphone() as source:
                        recognizer.adjust_for_ambient_noise(source, duration=0.5)
                        audio = recognizer.listen(source, timeout=5, phrase_time_limit=3)
                    if self._shutdown.is_set() or not self.config.get("enable_voice_commands", False):
                        continue
                    command = recognizer.recognize_google(audio).lower()
                    if "start dictation" in command:
                        self._post(self._voice_start_dictation)
                    elif "stop dictation" in command:
                        self._post(self._voice_stop_dictation)
                    elif "search now" in command:
                        self._post(self._on_search_hotkey)
                except (sr.UnknownValueError, sr.RequestError, sr.WaitTimeoutError):
                    continue
                except Exception as e:
                    self._diag("Voice command listener", "retrying in 5 s", e)
                    self._shutdown.wait(5)
        threading.Thread(target=listen_for_commands, name="vtta-voice", daemon=True).start()

    def _voice_start_dictation(self):
        if not self._rec:
            self._on_transcription_hotkey()

    def _voice_stop_dictation(self):
        if self._rec:
            self._on_transcription_hotkey()

    # ======================================================================
    # OS helpers & launch at sign-in
    # ======================================================================

    def open_app_folder(self):
        try:
            if IS_WIN:
                os.startfile(self.app_data_dir)
            elif IS_MAC:
                subprocess.Popen(["open", str(self.app_data_dir)])
            else:
                subprocess.Popen(["xdg-open", str(self.app_data_dir)])
        except Exception as e:
            self.handle_error("Can’t open folder", f"Couldn’t open {self.app_data_dir}.", details=e)

    def get_startup_folder(self):
        if IS_WIN:
            return Path(os.getenv("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        if IS_MAC:
            return Path.home() / "Library" / "LaunchAgents"
        return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"

    def startup_entry_path(self):
        if IS_WIN:
            return self.get_startup_folder() / f"{APP_NAME}.lnk"
        if IS_MAC:
            return self.get_startup_folder() / "com.vtta.voicetotextassistant.plist"
        return self.get_startup_folder() / "vtta.desktop"

    def startup_entry_exists(self):
        try:
            return self.startup_entry_path().exists()
        except Exception:
            return False

    def _launch_command(self):
        """Interpreter + script (or the packaged executable) that starts this copy of VTTA."""
        if getattr(sys, "frozen", False):
            return [str(Path(sys.executable).resolve())]
        exe = Path(sys.executable)
        if IS_WIN and exe.with_name("pythonw.exe").exists():
            exe = exe.with_name("pythonw.exe")  # no console window
        return [str(exe), str(Path(__file__).resolve())]

    def write_launcher_bat(self):
        """(Re)create start_minimized.bat beside the app. Paths inside the app folder are written relative
        to the .bat (%~dp0) so the whole folder can be moved or renamed."""
        def ref(p):
            p = Path(os.path.abspath(p))  # not resolve(): a venv's python is a symlink that must be kept
            try:
                return "%~dp0" + str(p.relative_to(self.script_dir))
            except ValueError:
                return str(p).replace("%", "%%")
        try:
            target_cmd = " ".join(f'"{ref(p)}"' for p in self._launch_command())
            start_flag = "/min " if self.config.get("start_minimized", True) else ""
            lines = [
                "@echo off",
                "setlocal",
                "REM Auto-generated launcher for VTTA (VoiceFlowAssistant). Recreated by the app; edits are overwritten.",
                "cd /d \"%~dp0\"",
                f"start \"\" {start_flag}{target_cmd}",
                "endlocal",
                "",
            ]
            self.launcher_bat.write_text("\r\n".join(lines), encoding="utf-8")
            return True
        except Exception as e:
            self._post(self.handle_error, "Startup launcher not written",
                       f"Couldn’t write {self.launcher_bat.name}.", True, e)
            return False

    def _create_windows_shortcut(self, lnk, target, workdir, icon):
        try:
            from win32com.client import Dispatch  # pywin32, if installed
            sc = Dispatch("WScript.Shell").CreateShortCut(str(lnk))
            sc.Targetpath, sc.WorkingDirectory, sc.IconLocation = str(target), str(workdir), str(icon)
            sc.WindowStyle = 7
            sc.save()
            return
        except ImportError:
            pass
        # No pywin32: let PowerShell create it. Paths travel in env vars, so no quoting problems.
        env = dict(os.environ, VTTA_LNK=str(lnk), VTTA_TARGET=str(target), VTTA_WD=str(workdir), VTTA_ICON=str(icon))
        ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:VTTA_LNK);"
              "$s.TargetPath=$env:VTTA_TARGET;$s.WorkingDirectory=$env:VTTA_WD;"
              "$s.IconLocation=$env:VTTA_ICON;$s.WindowStyle=7;$s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, check=True,
                       timeout=30, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def _write_startup_entry(self):
        path = self.startup_entry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if IS_WIN:
            if not self.write_launcher_bat():
                raise RuntimeError("launcher could not be written")
            ico = self.resource_dir / "assets" / "vtta.ico"
            self._create_windows_shortcut(path, self.launcher_bat, self.script_dir,
                                          ico if ico.exists() else Path(sys.executable))
        elif IS_MAC:
            with open(path, "wb") as f:
                plistlib.dump({"Label": "com.vtta.voicetotextassistant", "ProgramArguments": self._launch_command(),
                               "WorkingDirectory": str(self.script_dir), "RunAtLoad": True,
                               "ProcessType": "Interactive"}, f)
        else:
            cmd = " ".join('"' + p.replace('"', '\\"') + '"' for p in self._launch_command())
            icon = self.resource_dir / "assets" / "vtta.png"
            path.write_text("[Desktop Entry]\nType=Application\nName=VTTA\nComment=Voice to Text Assistant\n"
                            f"Exec={cmd}\nPath={self.script_dir}\nIcon={icon}\nTerminal=false\n"
                            "X-GNOME-Autostart-enabled=true\n", encoding="utf-8")
        self.config["startup_target"] = str(self.script_dir)

    def add_to_startup(self, quiet=False):
        try:
            self._write_startup_entry()
            self.save_config()
            if not quiet:
                self.update_status("VTTA will start when you sign in.")
            return True
        except Exception as e:
            self.handle_error("Startup not changed", "VTTA couldn’t be set to start at sign-in.", details=e)
            return False

    def remove_from_startup(self):
        try:
            self.startup_entry_path().unlink(missing_ok=True)
            self.update_status("VTTA will no longer start when you sign in.")
            return True
        except Exception as e:
            self.handle_error("Startup not changed", "VTTA couldn’t be removed from startup.", details=e)
            return False

    def _sync_startup_entry(self):
        """If launch-at-sign-in is on and the app folder moved, point the entry at the new location.
        Does nothing (no subprocess, no writes) when the entry is absent or already current."""
        try:
            if self.startup_entry_exists() and self.config.get("startup_target") != str(self.script_dir):
                self._write_startup_entry()
                self._post(self.save_config)
        except Exception as e:
            self._post(self._diag, "Startup entry refresh failed", "", e)

    # ======================================================================
    # Mainloop
    # ======================================================================

    def run(self):
        self.root.after(0, self._drain_ui_queue)
        try:
            self.root.mainloop()
        finally:
            self._shutting_down = True
            self._shutdown.set()
            if self.hotkeys is not None:
                self.hotkeys.shutdown()
            if self._tray_icon is not None:
                try:
                    self._tray_icon.stop()
                except Exception:
                    pass


class SettingsDialog:
    """Modal settings with validation next to each field. Nothing is applied until Save."""

    NUMERIC = [
        # key, tab, label, unit/help, kind, min, max, step
        ("silence_duration", "rec", "Stop after a pause of", "seconds", float, 0.5, 30, 0.5),
        ("silence_threshold_pct", "rec", "Silence threshold", "% of full scale — the tick on the level meter. "
         "Raise it in noisy rooms, lower it for quiet microphones.", float, 0.1, 20, 0.1),
        ("max_recording_seconds", "rec", "Longest recording", "seconds", int, 10, 1800, 10),
        ("unload_model_minutes", "rec", "Free model memory after", "idle minutes (0 = keep loaded). Saves "
         "hundreds of MB while you aren’t dictating; the next dictation reloads it.", int, 0, 1440, 5),
        ("browser_timeout_minutes", "search", "Close the private window after", "minutes (0 = never)",
         int, 0, 1440, 5),
        ("hotkey_press_debounce_ms", "adv", "Hotkey repeat guard", "ms — ignores key repeats right after a press",
         int, 0, 2000, 10),
        ("hotkey_post_debounce_ms", "adv", "Post-transcription guard", "ms — ignores stray hotkey events "
         "right after a result", int, 0, 2000, 10),
        ("overlay_blink_ms", "adv", "Indicator blink interval", "ms", int, 100, 2000, 50),
        ("overlay_flash_top_ms", "adv", "Indicator stays on top for", "ms after recording starts", int, 200, 10000, 100),
        ("hotkey_self_refresh_minutes", "adv", "Re-register hotkeys every", "minutes (0 = only after sleep). "
         "Applies after restart.", int, 0, 1440, 5),
        ("wake_gap_seconds", "adv", "Treat a pause longer than this as sleep", "seconds. Applies after restart.",
         int, 30, 3600, 10),
    ]

    def __init__(self, app):
        self.app = app
        px = app.px
        self.win = win = tk.Toplevel(app.root)
        app._settings_win = win
        win.withdraw()
        win.title("Settings — VTTA")
        win.configure(bg=C["bg"])
        win.transient(app.root)
        win.minsize(px(560), px(520))
        win.bind("<Escape>", lambda e: win.destroy())
        self.fields = {}      # key -> (var, widget, error_label, tab_index, spec)
        self.vars = {}
        self._install_proc = None
        cfg = app.config

        footer = ttk.Frame(win, padding=(px(16), px(12), px(16), px(16)))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(footer, text="Restore Defaults", style="Tertiary.TButton",
                   command=self.restore_defaults).pack(side=tk.LEFT)
        self.save_btn = ttk.Button(footer, text="Save", style="Primary.TButton", command=self.save)
        self.save_btn.pack(side=tk.RIGHT)
        ttk.Button(footer, text="Cancel", style="Secondary.TButton", command=win.destroy).pack(side=tk.RIGHT,
                                                                                               padx=(0, px(8)))
        self.form_error = ttk.Label(footer, text="", style="Error.TLabel")
        self.form_error.pack(side=tk.LEFT, padx=(px(10), 0))

        self.nb = ttk.Notebook(win)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=px(16), pady=(px(14), 0))
        self.tabs = {}
        for key, title in (("gen", "General"), ("rec", "Recording"), ("search", "Web Search"),
                           ("adv", "Advanced")):
            outer = tk.Frame(self.nb, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
            inner = self._scrollable(outer)
            inner.columnconfigure(1, weight=0)
            inner.columnconfigure(2, weight=1)
            self.nb.add(outer, text=title)
            self.tabs[key] = [inner, 0, len(self.tabs)]

        # ---- General ----
        self.section("gen", "Global hotkeys")
        self.hotkey_field("transcription_hotkey", "Dictate & paste", cfg["transcription_hotkey"])
        self.hotkey_field("search_hotkey", "Dictate a web search", cfg["search_hotkey"])
        self.note("gen", "Click Record and press the keys, or type them joined by + (e.g. ctrl+shift+r).")
        self.section("gen", "Feedback")
        self.check("gen", "sound_cues", "Sound cues for start, stop, done and errors", cfg["sound_cues"])
        self.check("gen", "click_sounds", "Soft click when pressing buttons in VTTA", cfg["click_sounds"])
        self.volume_row(cfg["sound_volume"])
        self.choice_row("gen", "overlay_position", "Recording indicator", OVERLAY_POSITIONS, cfg["overlay_position"],
                        "A small badge on screen while recording and transcribing, so you get feedback "
                        "without switching windows.")
        self.section("gen", "Window")
        where = "in the Dock" if IS_MAC else "in the system tray"
        tray_note = "" if app._can_hide() else "  The tray isn’t available on this system, so closing quits."
        self.check("gen", "close_to_tray", f"Closing the window keeps VTTA running {where}",
                   cfg["close_to_tray"], "When off, the close button quits VTTA and its hotkeys." + tray_note)
        self.check("gen", "start_minimized", "Start minimized", cfg["start_minimized"])
        signin = "Launch VTTA when I log in" if IS_MAC else "Launch VTTA when I sign in"
        self.check("gen", "_launch_at_startup", signin, app.startup_entry_exists())

        # ---- Recording ----
        self.section("rec", "Microphone")
        self.mic_row(cfg.get("input_device", ""))
        self.section("rec", "Auto-stop")
        self.note("rec", "Start Dictation in the window stops by itself when you pause. Hotkey dictation runs "
                         "until you press the hotkey again, unless you turn on the option below.")
        for spec in self.NUMERIC[:3]:
            self.number_field(*spec)
        self.check("rec", "auto_stop_hotkey", "Also auto-stop hotkey dictation after a pause", cfg["auto_stop_hotkey"])
        self.section("rec", "Memory")
        self.number_field(*self.NUMERIC[3])

        # ---- Search ----
        self.section("search", "Open search results in")
        self.vars["search_opener"] = tk.StringVar(value=cfg["search_opener"])
        t = self.tabs["search"]
        for val, label in (("playwright", "A private browser window — nothing touches your normal browser "
                                          "profile"),
                           ("default", "My default browser — fastest, no extra download")):
            ttk.Radiobutton(t[0], text=label, value=val, variable=self.vars["search_opener"],
                            style="Card.TRadiobutton", command=self.sync_dependents).grid(
                row=self.next_row("search"), column=0, columnspan=3, sticky="w", pady=(0, px(2)))
        self.section("search", "Private window")
        self.browser_install_row()
        self.path_field("browser_path", "Custom browser", cfg["browser_path"])
        self.number_field(*self.NUMERIC[4])

        # ---- Advanced ----
        self.section("adv", "Voice commands")
        self.check("adv", "enable_voice_commands", "Enable voice commands", cfg["enable_voice_commands"],
                   "Listens for “start dictation”, “stop dictation” and “search now”. "
                   "This keeps the microphone open all the time and sends short clips to Google’s online "
                   "recognizer. Dictation itself always stays on this computer.")
        self.section("adv", "Timing")
        for spec in self.NUMERIC[5:]:
            self.number_field(*spec)

        self.sync_dependents()
        win.bind("<Return>", lambda e: self.save() if not isinstance(e.widget, (ttk.Button, tk.Text)) else None)
        app.center_window(win, px(620), px(620))
        win.deiconify()
        win.grab_set()
        win.focus_set()

    # ---- layout helpers ----
    def _scrollable(self, outer):
        """A tab body that scrolls when the window is too small; the scrollbar only appears when needed."""
        px = self.app.px
        canvas = tk.Canvas(outer, bg=C["surface"], highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview, style="Slim.Vertical.TScrollbar")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = ttk.Frame(canvas, style="Card.TFrame", padding=(px(18), px(14)))
        win_id = canvas.create_window(0, 0, window=inner, anchor="nw")

        def relayout(_e=None):
            canvas.itemconfigure(win_id, width=canvas.winfo_width())
            canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), inner.winfo_reqheight()))
            need = inner.winfo_reqheight() > canvas.winfo_height() + 1
            if need and not sb.winfo_ismapped():
                sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, px(2)), pady=px(2), before=canvas)
            elif not need and sb.winfo_ismapped():
                sb.pack_forget()
                canvas.yview_moveto(0)
        inner.bind("<Configure>", relayout)
        canvas.bind("<Configure>", relayout)

        def wheel(e):
            if not sb.winfo_ismapped():
                return
            if getattr(e, "num", None) in (4, 5):
                step = -1 if e.num == 4 else 1
            else:
                step = -1 * (e.delta // 120 if IS_WIN else e.delta) if e.delta else 0
                step = int(_clamp(step, -3, 3)) or (-1 if e.delta > 0 else 1)
            canvas.yview_scroll(step, "units")
        for w in (canvas, inner):
            w.bind("<Enter>", lambda e: (self.win.bind_all("<MouseWheel>", wheel),
                                         self.win.bind_all("<Button-4>", wheel), self.win.bind_all("<Button-5>", wheel)))
            w.bind("<Leave>", lambda e: [self.win.unbind_all(s) for s in ("<MouseWheel>", "<Button-4>", "<Button-5>")])
        canvas.configure(yscrollincrement=px(20))
        return inner

    def next_row(self, tab):
        self.tabs[tab][1] += 1
        return self.tabs[tab][1] - 1

    def section(self, tab, text):
        r = self.next_row(tab)
        px = self.app.px
        frame = self.tabs[tab][0]
        if r:
            tk.Frame(frame, bg=C["border"], height=1).grid(row=r, column=0, columnspan=3, sticky="we",
                                                           pady=(px(12), px(12)))
            r = self.next_row(tab)
        ttk.Label(frame, text=text, style="Card.Section.TLabel").grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0, px(8)))

    def note(self, tab, text):
        ttk.Label(self.tabs[tab][0], text=text, style="Card.Muted.TLabel", wraplength=self.app.px(500),
                  justify=tk.LEFT).grid(row=self.next_row(tab), column=0, columnspan=3, sticky="w",
                                        pady=(0, self.app.px(8)))

    def check(self, tab, key, text, value, help_text=None, enabled=True):
        var = tk.BooleanVar(value=bool(value))
        self.vars[key] = var
        cb = ttk.Checkbutton(self.tabs[tab][0], text=text, variable=var, style="Card.TCheckbutton",
                             command=self.sync_dependents)
        cb.grid(row=self.next_row(tab), column=0, columnspan=3, sticky="w", pady=(0, self.app.px(3)))
        if not enabled:
            cb.state(["disabled"])
        if help_text:
            ttk.Label(self.tabs[tab][0], text=help_text, style="Card.Muted.TLabel", wraplength=self.app.px(470),
                      justify=tk.LEFT).grid(row=self.next_row(tab), column=0, columnspan=3, sticky="w",
                                            padx=(self.app.px(25), 0), pady=(0, self.app.px(8)))
        return cb

    def _label(self, tab, r, text):
        ttk.Label(self.tabs[tab][0], text=text, style="Card.TLabel").grid(row=r, column=0, sticky="w",
                                                                          padx=(0, self.app.px(14)),
                                                                          pady=self.app.px(3))

    def _error_label(self, tab):
        lbl = ttk.Label(self.tabs[tab][0], text="", style="Card.Error.TLabel")
        lbl.grid(row=self.next_row(tab), column=1, columnspan=2, sticky="w", pady=(0, self.app.px(4)))
        lbl.grid_remove()   # only takes space when there is an error
        return lbl

    def number_field(self, key, tab, label, unit, kind, lo, hi, step):
        px = self.app.px
        frame = self.tabs[tab][0]
        r = self.next_row(tab)
        self._label(tab, r, label)
        var = tk.StringVar(value=self.app._fmt_num(self.app.config[key]))
        sp = ttk.Spinbox(frame, from_=lo, to=hi, increment=step, textvariable=var, width=7)
        sp.grid(row=r, column=1, sticky="w", pady=px(3))
        ttk.Label(frame, text=unit, style="Card.Muted.TLabel", wraplength=px(270), justify=tk.LEFT).grid(
            row=r, column=2, sticky="w", padx=(px(10), 0))
        err = self._error_label(tab)
        self.fields[key] = (var, sp, err, self.tabs[tab][2], ("num", kind, lo, hi))
        var.trace_add("write", lambda *_: self.clear_error(key))

    def hotkey_field(self, key, label, value):
        px = self.app.px
        frame = self.tabs["gen"][0]
        r = self.next_row("gen")
        self._label("gen", r, label)
        var = tk.StringVar(value=value)
        ent = ttk.Entry(frame, textvariable=var, width=20)
        ent.grid(row=r, column=1, sticky="w", pady=px(3))
        btn = ttk.Button(frame, text="Record", style="Small.TButton", command=lambda: self.capture_hotkey(key, btn))
        btn.grid(row=r, column=2, sticky="w", padx=(px(8), 0))
        Tooltip(btn, "Click, then press the key combination you want (Esc cancels)")
        if self.app.hotkeys is None:
            btn.state(["disabled"])
        err = self._error_label("gen")
        self.fields[key] = (var, ent, err, 0, ("hotkey",))
        var.trace_add("write", lambda *_: self.clear_error(key))

    def volume_row(self, value):
        px = self.app.px
        frame = self.tabs["gen"][0]
        r = self.next_row("gen")
        self._label("gen", r, "Volume")
        var = tk.IntVar(value=int(value))
        self.vars["sound_volume"] = var
        box = ttk.Frame(frame, style="Card.TFrame")
        box.grid(row=r, column=1, columnspan=2, sticky="w", pady=px(3))
        pct = ttk.Label(box, text=f"{var.get()}%", style="Card.TLabel", width=5)
        scale = ttk.Scale(box, from_=0, to=100, orient=tk.HORIZONTAL, length=px(180),
                          command=lambda v: (var.set(int(round(float(v)))), pct.configure(text=f"{var.get()}%")))
        scale.set(var.get())
        scale.pack(side=tk.LEFT)
        pct.pack(side=tk.LEFT, padx=(px(8), 0))
        test = ttk.Button(box, text="▶ Test", style="Small.TButton", command=self.test_sound)
        test.pack(side=tk.LEFT, padx=(px(6), 0))
        Tooltip(test, "Play the “done” cue at this volume")
        self._volume_widgets = (scale, test)

    def test_sound(self):
        cfg = dict(self.app.config, sound_cues=True, sound_volume=self.vars["sound_volume"].get())
        saved = self.app.config
        self.app.config = cfg
        try:
            self.app.sounds.play("done")
        finally:
            self.app.config = saved

    def choice_row(self, tab, key, label, choices, value, help_text=None):
        px = self.app.px
        frame = self.tabs[tab][0]
        r = self.next_row(tab)
        self._label(tab, r, label)
        labels = [lab for _k, lab in choices]
        var = tk.StringVar(value=dict(choices).get(value, labels[0]))
        combo = ttk.Combobox(frame, textvariable=var, values=labels, state="readonly", width=16)
        combo.grid(row=r, column=1, columnspan=2, sticky="w", pady=px(3))
        self.vars[key] = var
        self._choices = getattr(self, "_choices", {})
        self._choices[key] = {lab: k for k, lab in choices}
        if help_text:
            ttk.Label(frame, text=help_text, style="Card.Muted.TLabel", wraplength=px(500), justify=tk.LEFT).grid(
                row=self.next_row(tab), column=0, columnspan=3, sticky="w", pady=(px(2), px(6)))

    def mic_row(self, current):
        px = self.app.px
        frame = self.tabs["rec"][0]
        r = self.next_row("rec")
        self._label("rec", r, "Input device")
        self.mic_var = tk.StringVar(value=current or "System default")
        box = ttk.Frame(frame, style="Card.TFrame")
        box.grid(row=r, column=1, columnspan=2, sticky="w", pady=px(3))
        self.mic_combo = ttk.Combobox(box, textvariable=self.mic_var, state="readonly", width=26,
                                      values=["System default"] + ([current] if current else []))
        self.mic_combo.pack(side=tk.LEFT)
        self.mic_refresh = ttk.Button(box, text="Refresh", style="Small.TButton", command=self.refresh_mics)
        self.mic_refresh.pack(side=tk.LEFT, padx=(px(8), 0))
        Tooltip(self.mic_refresh, "Look for microphones again (e.g. after plugging one in)")
        self.mic_note = ttk.Label(frame, text="", style="Card.Muted.TLabel", wraplength=px(500), justify=tk.LEFT)
        self.mic_note.grid(row=self.next_row("rec"), column=0, columnspan=3, sticky="w", pady=(px(2), px(4)))
        self.refresh_mics()

    def refresh_mics(self):
        """Lists devices once, in the background, only when this dialog asks."""
        self.mic_refresh.state(["disabled"])
        self.mic_note.configure(text="Looking for microphones…")
        app = self.app

        def work():
            names = app.list_input_devices()
            app._post(done, names)

        def done(names):
            if not self.win.winfo_exists():
                return
            cur = self.mic_var.get()
            values = ["System default"] + names
            if cur not in values:
                values.append(cur)
                self.mic_note.configure(text=f"“{cur}” isn’t connected right now; the system default "
                                             "is used until it is.")
            else:
                self.mic_note.configure(text=f"{len(names)} input device{'s' if len(names) != 1 else ''} found. "
                                             "The microphone is only opened while you record.")
            self.mic_combo.configure(values=values)
            self.mic_refresh.state(["!disabled"])
        threading.Thread(target=work, daemon=True).start()

    def path_field(self, key, label, value):
        px = self.app.px
        frame = self.tabs["search"][0]
        r = self.next_row("search")
        self._label("search", r, label)
        var = tk.StringVar(value=value)
        box = ttk.Frame(frame, style="Card.TFrame")
        box.grid(row=r, column=1, columnspan=2, sticky="w", pady=px(3))
        ent = ttk.Entry(box, textvariable=var, width=26)
        ent.pack(side=tk.LEFT)

        def browse():
            p = filedialog.askopenfilename(parent=self.win, title="Choose a Chromium-based browser")
            if p:
                var.set(p)
        self.browse_btn = ttk.Button(box, text="Browse…", style="Small.TButton", command=browse)
        self.browse_btn.pack(side=tk.LEFT, padx=(px(8), 0))
        ttk.Label(frame, text="Optional: Chrome, Edge or Brave program file. Leave empty to use the browser above.",
                  style="Card.Muted.TLabel", wraplength=px(500), justify=tk.LEFT).grid(
            row=self.next_row("search"), column=0, columnspan=3, sticky="w", pady=(0, px(4)))
        err = self._error_label("search")
        self.fields[key] = (var, ent, err, 2, ("path",))
        var.trace_add("write", lambda *_: self.clear_error(key))

    def browser_install_row(self):
        px = self.app.px
        frame = self.tabs["search"][0]
        r = self.next_row("search")
        self._label("search", r, "Browser")
        box = ttk.Frame(frame, style="Card.TFrame")
        box.grid(row=r, column=1, columnspan=2, sticky="w", pady=px(3))
        self.install_btn = ttk.Button(box, text="Install into app folder", style="Small.TButton",
                                      command=self.toggle_install)
        self.install_btn.pack(side=tk.LEFT)
        Tooltip(self.install_btn, "Downloads Playwright’s Chromium (about 150 MB) into "
                                  f"{DATA_DIR_NAME}/browsers, so the private window needs nothing outside this folder.")
        self.install_status = ttk.Label(frame, text="", style="Card.Muted.TLabel", wraplength=px(480),
                                        justify=tk.LEFT)
        self.install_status.grid(row=self.next_row("search"), column=0, columnspan=3, sticky="w", pady=(0, px(6)))
        self._update_install_status()

    def _update_install_status(self, state=None, text=None):
        if not self.win.winfo_exists():
            return
        running = self._install_proc is not None and self._install_proc.poll() is None
        if state in ("done", "failed"):
            self._install_proc = None
            running = False
        if text:
            self.install_status.configure(text=text, style="Card.Error.TLabel" if state == "failed" else "Card.Muted.TLabel")
        elif self.app.local_browser_installed():
            self.install_status.configure(text="✓ Installed in the app folder", style="Card.Muted.TLabel")
        else:
            self.install_status.configure(text="Not installed in the app folder. A copy installed earlier with "
                                               "\u201cpython -m playwright install\u201d is used if present.",
                                          style="Card.Muted.TLabel")
        self.install_btn.configure(text="Cancel install" if running else
                                   ("Reinstall" if self.app.local_browser_installed() else "Install into app folder"))

    def toggle_install(self):
        if self._install_proc is not None and self._install_proc.poll() is None:
            self._install_proc.terminate()
            self._update_install_status("failed", "Install cancelled.")
            return
        self._update_install_status(None, "Starting…")
        self._install_proc = self.app.install_private_browser(self._update_install_status)
        self._update_install_status(None, "Downloading…" if self._install_proc else None)

    # ---- behaviour ----
    def sync_dependents(self):
        private = self.vars["search_opener"].get() == "playwright"
        for key in ("browser_path", "browser_timeout_minutes"):
            w = self.fields[key][1]
            w.state(["!disabled"] if private else ["disabled"])
            if not private:
                self.clear_error(key)
        self.browse_btn.state(["!disabled"] if private else ["disabled"])
        self.install_btn.state(["!disabled"] if private else ["disabled"])
        any_sound = self.vars["sound_cues"].get() or self.vars["click_sounds"].get()
        for w in self._volume_widgets:
            w.state(["!disabled"] if any_sound else ["disabled"])

    def capture_hotkey(self, key, btn):
        app = self.app
        var = self.fields[key][0]
        old = var.get()
        btn.configure(text="Press keys…")
        btn.state(["disabled"])
        app._capturing_hotkey = True

        def done(hk):
            app._capturing_hotkey = False
            if not btn.winfo_exists():
                return
            btn.configure(text="Record")
            btn.state(["!disabled"])
            var.set(hk if hk and hk != "esc" else old)
        app.hotkeys.capture(lambda hk: app._post(done, hk))

    def clear_error(self, key):
        var, w, err, _tab, _spec = self.fields[key]
        err.configure(text="")
        err.grid_remove()
        w.configure(style="TSpinbox" if isinstance(w, ttk.Spinbox) else "TEntry")
        self.form_error.configure(text="")

    def set_error(self, key, msg):
        var, w, err, _tab, _spec = self.fields[key]
        err.configure(text=msg)
        err.grid()
        w.configure(style="Invalid.TSpinbox" if isinstance(w, ttk.Spinbox) else "Invalid.TEntry")

    def restore_defaults(self):
        for key, (var, *_rest) in self.fields.items():
            d = DEFAULT_CONFIG[key]
            var.set(self.app._fmt_num(d) if isinstance(d, (int, float)) and not isinstance(d, bool) else d)
        for key, var in self.vars.items():
            if key in DEFAULT_CONFIG:
                d = DEFAULT_CONFIG[key]
                if key in getattr(self, "_choices", {}):
                    d = {k: lab for lab, k in self._choices[key].items()}[d]
                var.set(d)
        vol = self._volume_widgets[0]
        vol.set(DEFAULT_CONFIG["sound_volume"])
        self.mic_var.set("System default")
        self.sync_dependents()
        self.form_error.configure(text="Defaults restored — click Save to apply.", style="Muted.TLabel")

    def validate(self):
        out, errors = {}, []
        private = self.vars["search_opener"].get() == "playwright"
        for key, (var, w, _err, tab, spec) in self.fields.items():
            raw = var.get().strip()
            msg = None
            if spec[0] == "num":
                _, kind, lo, hi = spec
                try:
                    v = float(raw)
                    if not math.isfinite(v):
                        raise ValueError
                except ValueError:
                    v, msg = None, "Enter a number."
                if v is not None:
                    if kind is int and v != int(v):
                        msg = "Enter a whole number."
                    elif not lo <= v <= hi:
                        msg = f"Enter a value from {self.app._fmt_num(lo)} to {self.app._fmt_num(hi)}."
                    else:
                        out[key] = int(v) if kind is int else v
                if msg and key == "browser_timeout_minutes" and not private:
                    msg, out[key] = None, self.app.config[key]
            elif spec[0] == "hotkey":
                hk = raw.lower().replace(" ", "")
                if not hk:
                    msg = "Enter a hotkey."
                elif self.app.hotkeys is not None:
                    msg = self.app.hotkeys.validate(hk)
                out[key] = hk
            elif spec[0] == "path":
                if raw and private and not os.path.isfile(raw):
                    msg = "File not found. Choose the browser’s program file, or leave this empty."
                out[key] = raw
            if msg:
                errors.append((tab, key, msg))
        if "search_hotkey" not in [k for _t, k, _m in errors] and \
                out.get("transcription_hotkey") == out.get("search_hotkey"):
            errors.append((0, "search_hotkey", "The two hotkeys must be different."))
        for key, var in self.vars.items():
            val = var.get()
            if key in getattr(self, "_choices", {}):
                val = self._choices[key].get(val, DEFAULT_CONFIG[key])
            out[key] = val
        mic = self.mic_var.get()
        out["input_device"] = "" if mic == "System default" else mic
        return out, errors

    def save(self):
        out, errors = self.validate()
        for _tab, key, msg in errors:
            self.set_error(key, msg)
        if errors:
            tab, key, _ = errors[0]
            self.nb.select(tab)
            self.fields[key][1].focus_set()
            self.form_error.configure(text=f"Please fix {len(errors)} field{'s' if len(errors) > 1 else ''}.",
                                      style="Error.TLabel")
            self.app.sounds.play("error")
            return
        err = self.app.apply_settings(out)
        if err:
            self.nb.select(0)
            self.set_error("transcription_hotkey", err)
            return
        self.win.destroy()


if __name__ == "__main__":
    app = VoiceFlowAssistant()
    app.run()
