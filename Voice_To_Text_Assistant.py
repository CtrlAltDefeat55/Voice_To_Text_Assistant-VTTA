import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import pyaudio
import wave
import threading
import time
import os
import json
import sqlite3
from datetime import datetime
import pyautogui
import pyperclip
import keyboard
import whisper
import numpy as np
from playwright.sync_api import sync_playwright
import speech_recognition as sr
import sys
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw
import pystray

# --- NEW: optional platformdirs for cross-platform, user-writable app data dir
try:
    from platformdirs import user_data_dir as _user_data_dir
except Exception:
    _user_data_dir = None


class VoiceFlowAssistant:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("VoiceFlow Assistant (Dictation Mode)")
        self.root.geometry("700x850")

        # --- App naming early (used for data dir)
        self.app_name = "VoiceFlowAssistant"

        # --- Correctly get the script's directory (used for launcher, etc.)
        if getattr(sys, 'frozen', False):
            self.script_dir = Path(sys.executable).parent
        else:
            self.script_dir = Path(__file__).parent.resolve()

        # --- CHANGED: user-writable app data dir via platformdirs (with fallback)
        if _user_data_dir:
            # e.g. Windows: %APPDATA%\VoiceFlowAssistant, macOS: ~/Library/Application Support/VoiceFlowAssistant
            # Linux: ~/.local/share/VoiceFlowAssistant
            self.app_data_dir = Path(_user_data_dir(self.app_name, "VoiceFlow"))
        else:
            # Fallback to original behavior next to the script/exe
            self.app_data_dir = self.script_dir / "VoiceFlow_App_Files"
        self.app_data_dir.mkdir(parents=True, exist_ok=True)

        self.status_var = tk.StringVar(value="Initializing...")

        # --- State Management ---
        self.is_recording = False
        self.is_search_recording = False
        self.action_after_transcription = None
        self.tray_icon = None

        # Debounce control
        self.debounce_until = 0.0                # ignore any hotkey before this time (monotonic)
        self.debounce_press_until = 0.0          # immediate press debounce window

        # --- Overlay state (bottom-right indicator window) ---
        self.overlay_win = None
        self.overlay_canvas = None
        self.overlay_dot_id = None
        self.overlay_text_id = None
        self.overlay_blink_after_id = None
        self.overlay_blink_on = True
        self.overlay_top_flash_after_id = None   # cancels scheduled topmost->false

        # --- Configuration & files ---
        self.config_file = self.app_data_dir / "config.json"
        self.history_db = self.app_data_dir / "history.db"
        self.error_log_file = self.app_data_dir / "error.log"
        self.temp_audio_file = self.app_data_dir / "temp_recording.wav"
        self.launcher_bat = self.script_dir / "start_minimized.bat"  # auto (re)generated for Startup

        self.load_config()
        self.init_database()

        # --- Audio variables ---
        self.audio_data = []
        self.silence_counter = 0
        self.silence_threshold = 0.01
        self.silence_duration = self.config.get("silence_duration", 2)
        self.sample_rate = 16000
        self.chunk_size = 1024
        self.max_recording_time = 300

        self.whisper_model = None
        self.voice_recognizer = sr.Recognizer()
        self.voice_commands_active = True

        # --- UI State Variables ---
        self.write_to_cursor_var = tk.BooleanVar(value=True)
        self.record_history_var = tk.BooleanVar(value=False)

        # --- Hotkey Handlers ---
        self.transcription_hotkey_handler = None
        self.search_hotkey_handler = None

        self.create_widgets()
        self.setup_hotkeys()
        self.start_hotkey_watchdog()  # <<-- ADDED: start watchdog after setting hotkeys

        self.load_whisper_model()
        self.start_voice_command_listener()

        # --- System Tray Setup ---
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        threading.Thread(target=self.setup_tray_icon, daemon=True).start()

        # --- Start minimized behavior (configurable) ---
        if self.config.get("start_minimized", True):
            self.root.iconify()
            self.update_status("Idle. Minimized to taskbar.")
        else:
            self.update_status("Idle.")

    # ---------------- UI ----------------

    def create_tray_icon_image(self):
        width, height = 64, 64
        image = Image.new('RGB', (width, height), 'black')
        dc = ImageDraw.Draw(image)
        dc.rectangle([(width // 4, height // 4), (width * 3 // 4, height * 3 // 4)], fill='white')
        return image

    def setup_tray_icon(self):
        icon_image = self.create_tray_icon_image()
        menu = (
            pystray.MenuItem('Show', self.show_window, default=True),
            pystray.MenuItem('Reload hotkeys', self.tray_reload_hotkeys),  # <<-- ADDED
            pystray.MenuItem('Exit', self.quit_app)
        )
        self.tray_icon = pystray.Icon(self.app_name, icon_image, self.app_name, menu)
        self.tray_icon.run()

    def tray_reload_hotkeys(self, icon, item):  # <<-- ADDED
        # ensure we run on Tk thread
        self.root.after(0, lambda: self.refresh_hotkeys(reason="tray"))

    def hide_window(self):
        self.root.withdraw()

    def show_window(self, icon=None, item=None):
        self.root.deiconify()

    def quit_app(self, icon=None, item=None):
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.quit()

    def _set_indicator(self, state: str):
        """Small LED in bottom-right of the app: idle=gray, recording=green, transcribing=orange, error=red."""
        color = {"idle": "gray", "recording": "green", "transcribing": "orange", "error": "red"}.get(state, "gray")
        if hasattr(self, "rec_dot") and self.rec_dot:
            self.rec_dot.delete("all")
            self.rec_dot.create_oval(2, 2, 10, 10, fill=color, outline=color)

    def create_widgets(self):
        # Main layout
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Buttons row
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(0, 10))

        self.start_button = ttk.Button(button_frame, text="Start Recording", command=self.toggle_recording_with_silence)
        self.start_button.pack(side=tk.LEFT, padx=(0, 5))

        self.stop_button = ttk.Button(button_frame, text="Stop Recording", command=self.stop_recording, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=(0, 5))

        self.search_button = ttk.Button(button_frame, text="Web Search", command=self.perform_search_from_button)
        self.search_button.pack(side=tk.LEFT, padx=(0, 5))

        # <<-- ADDED: manual hotkey reload button
        self.reload_hotkeys_btn = ttk.Button(button_frame, text="Reload Hotkeys",
                                             command=lambda: self.refresh_hotkeys(reason="manual"))
        self.reload_hotkeys_btn.pack(side=tk.LEFT, padx=(5, 0))

        history_frame = ttk.Frame(button_frame)
        history_frame.pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(history_frame, text="View History", command=self.show_history).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Checkbutton(history_frame, text="Record History", variable=self.record_history_var).pack(side=tk.LEFT, padx=(5,0))
        ttk.Button(history_frame, text="Clear History", command=self.clear_history).pack(side=tk.LEFT, padx=(5,0))

        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.pack(fill=tk.X, pady=(0, 10))

        transcription_options_frame = ttk.Frame(main_frame)
        transcription_options_frame.pack(fill=tk.X)
        ttk.Label(transcription_options_frame, text="Transcribed Text:").pack(anchor=tk.W, side=tk.LEFT)
        ttk.Checkbutton(transcription_options_frame, text="Write to cursor", variable=self.write_to_cursor_var).pack(anchor=tk.W, side=tk.LEFT, padx=10)

        self.text_editor = scrolledtext.ScrolledText(main_frame, height=12, wrap=tk.WORD)
        self.text_editor.pack(fill=tk.BOTH, expand=True, pady=(5, 10))

        model_frame = ttk.Frame(main_frame)
        model_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(model_frame, text="Whisper Model:").pack(side=tk.LEFT)
        self.model_var = tk.StringVar(value=self.config.get("whisper_model", "base"))
        model_combo = ttk.Combobox(model_frame, textvariable=self.model_var, values=["tiny", "base", "small", "medium", "large"], state="readonly", width=10)
        model_combo.pack(side=tk.LEFT, padx=(5, 0))
        model_combo.bind("<<ComboboxSelected>>", self.change_model)

        lang_frame = ttk.Frame(main_frame)
        lang_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(lang_frame, text="Language:").pack(side=tk.LEFT)
        self.lang_var = tk.StringVar(value=self.config.get("language", "en"))
        lang_combo = ttk.Combobox(lang_frame, textvariable=self.lang_var, values=["en", "es", "fr", "de", "it", "pt", "ru", "ja", "ko", "zh"], state="readonly", width=10)
        lang_combo.pack(side=tk.LEFT, padx=(5, 0))
        lang_combo.bind("<<ComboboxSelected>>", self.change_language)

        system_frame = ttk.Frame(main_frame)
        system_frame.pack(fill=tk.X, pady=(10, 0), anchor=tk.W)
        ttk.Button(system_frame, text="Add to Startup", command=self.add_to_startup).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(system_frame, text="Remove from Startup", command=self.remove_from_startup).pack(side=tk.LEFT)
        ttk.Button(system_frame, text="Open App Folder", command=self.open_app_folder).pack(side=tk.LEFT, padx=(5, 0))
        settings_button = ttk.Button(main_frame, text="Settings", command=self.open_settings)
        settings_button.pack(anchor=tk.E, pady=(10, 0))

        # Status area with LED (bottom)
        status_frame = ttk.Frame(self.root)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)

        self.status_bar = tk.Label(status_frame, textvariable=self.status_var, bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.rec_dot = tk.Canvas(status_frame, width=12, height=12, highlightthickness=0)
        self.rec_dot.pack(side=tk.RIGHT, padx=6, pady=4)

        self._set_indicator("idle")

    def update_status(self, status):
        try:
            if hasattr(self, 'status_var') and self.status_var:
                self.status_var.set(f"[{datetime.now().strftime('%H:%M:%S')}] {status}")
                self.root.update_idletasks()
        except Exception:
            pass

    # ---------------- Config & DB ----------------

    def load_config(self):
        default_config = {
            "transcription_hotkey": "ctrl+shift+r",
            "search_hotkey": "ctrl+shift+s",
            "silence_duration": 2,
            "whisper_model": "base",
            "language": "en",
            "browser_path": "",
            "browser_timeout_minutes": 60,
            "hotkey_press_debounce_ms": 250,   # ignore repeats on first press
            "hotkey_post_debounce_ms": 150,    # ignore stray events after transcription finishes
            "overlay_blink_ms": 500,           # blink speed for the overlay LED
            "overlay_flash_top_ms": 800,       # how long overlay stays topmost on each start
            "start_minimized": True,           # whether the Tk window starts minimized (toggle in Settings)
            # ---- ADDED defaults ----
            "hotkey_self_refresh_minutes": 60, # periodically re-register hotkeys
            "wake_gap_seconds": 180            # if sleep/hibernation gap is detected, refresh
        }
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    self.config = json.load(f)
                for key, value in default_config.items():
                    self.config.setdefault(key, value)
            except Exception as e:
                self.config = default_config
                self.handle_error("Config Load Error", f"Could not read config, loaded defaults. Error: {e}", show_message=False)
        else:
            self.config = default_config
            self.save_config()

    def save_config(self):
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            self.handle_error("Config Save Error", e, show_message=False)

    def init_database(self):
        try:
            with sqlite3.connect(self.history_db) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT,
                        text TEXT,
                        type TEXT
                    )
                ''')
                conn.commit()
        except Exception as e:
            self.handle_error("Database Init Error", e, show_message=False)

    # ---------------- Launcher (.bat) for Startup ----------------

    def write_launcher_bat(self):
        """
        (Re)create start_minimized.bat alongside the script/exe.
        Honors config['start_minimized'] to choose /MIN or normal window, and
        targets the correct interpreter/exe depending on frozen or script mode.
        """
        try:
            # Build target command
            if getattr(sys, 'frozen', False):
                # Packaged exe
                target_cmd = f"\"{sys.executable}\""
            else:
                # Running from source
                pyexe = sys.executable  # python.exe
                script_path = Path(__file__).resolve()
                target_cmd = f"\"{pyexe}\" \"{script_path}\""

            # Choose start flags based on config
            start_flag = "/min " if self.config.get("start_minimized", True) else ""
            lines = [
                "@echo off",
                "setlocal",
                "REM Auto-generated launcher for VoiceFlowAssistant",
                "REM To change minimized behavior, use Settings → Start Minimized in the app.",
                "cd /d \"%~dp0\"",
                f"start \"\" {start_flag}{target_cmd}",
                "endlocal",
                ""
            ]
            self.launcher_bat.write_text("\n".join(lines), encoding="utf-8")
            return True
        except Exception as e:
            self.handle_error("Launcher Write Error", e)
            return False

    # ---------------- Model ----------------

    def load_whisper_model(self):
        model_name = self.config.get("whisper_model", "base")
        try:
            self.update_status(f"Loading Whisper model: {model_name}...")
            self.whisper_model = whisper.load_model(model_name)
            self.update_status("Model loaded successfully")
        except Exception as e:
            error_msg = f"Failed to load Whisper model '{model_name}'. Falling back to 'base'. Error: {e}"
            self.handle_error("Model Error", error_msg, show_message=True)
            try:
                self.whisper_model = whisper.load_model("base")
                self.update_status("Fallback model 'base' loaded")
            except Exception as e2:
                error_msg2 = f"CRITICAL: Failed to load any Whisper model. Transcription is disabled. Error: {e2}"
                self.handle_error("Critical Model Error", error_msg2, show_message=True)
                self.whisper_model = None

    # ---------------- Hotkeys ----------------

    def setup_hotkeys(self):
        try:
            if self.transcription_hotkey_handler:
                keyboard.remove_hotkey(self.transcription_hotkey_handler)
            if self.search_hotkey_handler:
                keyboard.remove_hotkey(self.search_hotkey_handler)
        except Exception:
            pass
        try:
            trans_key = self.config.get("transcription_hotkey", "ctrl+shift+r")
            search_key = self.config.get("search_hotkey", "ctrl+shift+s")

            # Use press-based triggers (compatible across keyboard versions)
            self.transcription_hotkey_handler = keyboard.add_hotkey(
                trans_key, self._on_transcription_hotkey
            )
            self.search_hotkey_handler = keyboard.add_hotkey(
                search_key, self._on_search_hotkey
            )
            self.update_status(f"Hotkeys set: Dictate[{trans_key}], Search[{search_key}]")
        except Exception as e:
            self.handle_error("Hotkey Set Error", e, show_message=True)

    # --------- ADDED: self-healing hotkey refresh & watchdog ----------

    def refresh_hotkeys(self, reason="auto"):
        """
        Safely unhook/re-register hotkeys. Skips if currently recording.
        """
        if self.is_recording:
            return
        try:
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:
                # Fallback clears any stray handlers
                try:
                    keyboard.unhook_all()
                except Exception:
                    pass

            self.transcription_hotkey_handler = None
            self.search_hotkey_handler = None
            self.setup_hotkeys()
            self.update_status(f"Hotkeys reloaded ({reason}).")
        except Exception as e:
            self.handle_error("Hotkeys Refresh Error", e, show_message=False)

    def start_hotkey_watchdog(self):
        """
        Background loop that refreshes hotkeys (a) on large time gaps (wake from sleep),
        and (b) periodically while idle.
        """
        def loop():
            try:
                refresh_mins = int(self.config.get("hotkey_self_refresh_minutes", 60))
            except Exception:
                refresh_mins = 60
            try:
                wake_gap = int(self.config.get("wake_gap_seconds", 180))
            except Exception:
                wake_gap = 180

            last = time.monotonic()
            next_refresh_at = last + max(1, refresh_mins) * 60

            while True:
                time.sleep(30)
                now = time.monotonic()

                # Detect sleep/hibernate or long stall
                if now - last > wake_gap:
                    self.root.after(0, lambda: self.refresh_hotkeys(reason="resume/wake"))

                # Periodic refresh only when we're idle
                if now >= next_refresh_at:
                    if not self.is_recording and self.action_after_transcription is None:
                        self.root.after(0, lambda: self.refresh_hotkeys(reason="periodic"))
                    next_refresh_at = now + max(1, refresh_mins) * 60

                last = now

        threading.Thread(target=loop, daemon=True).start()

    # ---------------- Recording ----------------

    def _press_debounced(self) -> bool:
        """Return True if we should proceed; False if still in immediate-press debounce."""
        now = time.monotonic()
        if now < self.debounce_press_until or now < self.debounce_until:
            return False
        # set immediate press debounce
        try:
            ms = int(self.config.get("hotkey_press_debounce_ms", 250))
        except Exception:
            ms = 250
        self.debounce_press_until = now + (ms / 1000.0)
        return True

    def _on_transcription_hotkey(self):
        if not self._press_debounced():
            return
        self.toggle_dictation_recording()

    def _on_search_hotkey(self):
        if not self._press_debounced():
            return
        self.toggle_search_recording()

    def start_recording(self, with_silence_detection=True):
        if self.is_recording:
            return
        self.is_recording = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.progress.start()
        self._set_indicator("recording")
        self._overlay_show_recording()  # show overlay & briefly make it topmost on every start
        self.update_status("Recording with silence detection..." if with_silence_detection else "Recording... Press hotkey to stop.")
        threading.Thread(target=self.record_audio, args=(with_silence_detection,), daemon=True).start()

    def stop_recording(self):
        self.is_recording = False

    def record_audio(self, with_silence_detection=True):
        self.audio_data = []
        self.silence_counter = 0
        start_time = time.time()
        try:
            p = pyaudio.PyAudio()
            stream = p.open(format=pyaudio.paInt16, channels=1, rate=self.sample_rate, input=True, frames_per_buffer=self.chunk_size)
        except Exception as e:
            self.root.after(0, lambda: self.handle_error("Audio Device Error", e))
            self.root.after(0, self.reset_ui_after_recording)
            return
        while self.is_recording and time.time() - start_time < self.max_recording_time:
            data = stream.read(self.chunk_size, exception_on_overflow=False)
            self.audio_data.append(data)
            if with_silence_detection:
                audio_chunk = np.frombuffer(data, dtype=np.int16)
                volume = np.abs(audio_chunk).mean() if audio_chunk.size > 0 else 0
                if volume < self.silence_threshold * 100:
                    self.silence_counter += 1
                else:
                    self.silence_counter = 0
                if self.silence_counter > (self.silence_duration * self.sample_rate / self.chunk_size):
                    self.update_status("Silence detected, stopping...")
                    break
        if self.is_recording and time.time() - start_time >= self.max_recording_time:
            self.update_status("Recording timeout reached")
        self.is_recording = False
        stream.stop_stream()
        stream.close()
        p.terminate()
        self.root.after(0, self.reset_ui_after_recording)
        self.root.after(0, self.process_audio)

    def reset_ui_after_recording(self):
        self.is_search_recording = False
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.progress.stop()
        self._overlay_hide()  # hide overlay once we stop capturing audio
        self.update_status("Processing audio...")

    def process_audio(self):
        if not self.audio_data:
            self.update_status("Idle: No audio recorded")
            self._set_indicator("idle")
            return
        try:
            with wave.open(str(self.temp_audio_file), 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(pyaudio.PyAudio().get_sample_size(pyaudio.paInt16))
                wf.setframerate(self.sample_rate)
                wf.writeframes(b''.join(self.audio_data))
        except Exception as e:
            self.handle_error("File Write Error", e)
            self.update_status("Idle")
            self._set_indicator("idle")
            return
        if not self.whisper_model:
            self.handle_error("Transcription Error", "Whisper model is not loaded.", True)
            self.update_status("Idle")
            self._set_indicator("idle")
            return
        try:
            self.update_status("Transcribing...")
            self._set_indicator("transcribing")
            result = self.whisper_model.transcribe(str(self.temp_audio_file), language=self.config.get("language", "en"))
            text = result["text"].strip()
            self.text_editor.delete(1.0, tk.END)
            if text:
                self.text_editor.insert(tk.END, text)
                self.save_to_history(text, "transcription")
                self.update_status("Transcription complete")
                if self.action_after_transcription == "paste" and self.write_to_cursor_var.get():
                    self.insert_text_at_cursor(text)
                elif self.action_after_transcription == "search":
                    self.perform_search_from_text(text)
            else:
                self.update_status("Transcription complete: No speech detected")
            self.action_after_transcription = None
        except Exception as e:
            self.handle_error("Transcription Error", e)
        finally:
            self.update_status("Idle")
            try:
                self.temp_audio_file.unlink(missing_ok=True)
            except Exception:
                pass
            # back to idle LED
            self._set_indicator("idle")
            # short post-transcription debounce so stray hotkey events don't immediately restart
            try:
                post_ms = int(self.config.get("hotkey_post_debounce_ms", 150))
            except Exception:
                post_ms = 150
            self.debounce_until = time.monotonic() + (post_ms / 1000.0)

    def insert_text_at_cursor(self, text):
        if not text:
            return
        try:
            self.update_status("Pasting text...")
            time.sleep(0.2)
            pyperclip.copy(text)
            # --- CHANGED: cross-platform paste shortcut
            mod = 'command' if sys.platform == 'darwin' else 'ctrl'
            pyautogui.hotkey(mod, 'v')
            self.update_status("Pasting complete.")
        except Exception as e:
            self.handle_error("Paste Error", e)

    def toggle_dictation_recording(self):
        if self.is_recording:
            self.action_after_transcription = "paste" if self.write_to_cursor_var.get() else None
            self.stop_recording()
        else:
            self.action_after_transcription = None
            self.start_recording(False)

    def toggle_search_recording(self):
        if self.is_recording:
            self.action_after_transcription = "search"
            self.stop_recording()
        else:
            self.action_after_transcription = None
            self.start_recording(False)

    def toggle_recording_with_silence(self):
        if self.is_recording:
            self.stop_recording()
        else:
            self.action_after_transcription = None
            self.start_recording(True)

    # ---------------- Overlay (bottom-right transient indicator) ----------------

    def _overlay_show_recording(self):
        """Show a small, borderless window in the bottom-right that blinks green while recording.
        On every start, briefly make it topmost to ensure visibility, then drop back so it can be covered.
        """
        try:
            width, height = 180, 64
            margin_x, margin_y = 16, 48  # bottom margin ~taskbar
            bg = '#111111'

            # Create window if needed
            created = False
            if self.overlay_win is None or not self.overlay_win.winfo_exists():
                self.overlay_win = tk.Toplevel(self.root)
                self.overlay_win.overrideredirect(True)  # no border/title
                try:
                    self.overlay_win.attributes('-alpha', 0.92)
                except Exception:
                    pass
                self.overlay_win.configure(bg=bg)

                self.overlay_canvas = tk.Canvas(self.overlay_win, width=width, height=height,
                                                bg=bg, highlightthickness=0, bd=0)
                self.overlay_canvas.pack(fill=tk.BOTH, expand=True)

                self.overlay_dot_id = self.overlay_canvas.create_oval(16, 16, 40, 40,
                                                                      fill="green", outline="green", width=2)
                self.overlay_text_id = self.overlay_canvas.create_text(56, 28, anchor='w',
                                                                       text="Recording…",
                                                                       fill="#e6e6e6",
                                                                       font=("Segoe UI", 11, "bold"))
                created = True

            # Position bottom-right
            self.overlay_win.update_idletasks()
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x = sw - width - margin_x
            y = sh - height - margin_y
            self.overlay_win.geometry(f"{width}x{height}+{x}+{y}")

            # Always show and bring to front on start
            self.overlay_win.deiconify()
            self.overlay_win.lift()
            # Make it topmost briefly to guarantee you see it, then drop back
            try:
                self.overlay_win.attributes('-topmost', True)
            except Exception:
                pass

            # Cancel any existing scheduled drop
            if self.overlay_top_flash_after_id:
                try:
                    self.overlay_win.after_cancel(self.overlay_top_flash_after_id)
                except Exception:
                    pass
                self.overlay_top_flash_after_id = None

            flash_ms = int(self.config.get("overlay_flash_top_ms", 800))
            self.overlay_top_flash_after_id = self.overlay_win.after(flash_ms, self._overlay_end_top_flash)

            # Start/continue blinking
            self.overlay_blink_on = True
            if created or not self.overlay_blink_after_id:
                self._overlay_blink_tick()
        except Exception as e:
            # Overlay is non-critical; log but don't interrupt flow
            self.handle_error("Overlay Show Error", e, show_message=False)

    def _overlay_end_top_flash(self):
        # Drop topmost so user windows can cover it
        if self.overlay_win and self.overlay_win.winfo_exists():
            try:
                self.overlay_win.attributes('-topmost', False)
            except Exception:
                pass
        self.overlay_top_flash_after_id = None

    def _overlay_hide(self):
        try:
            # Cancel blink
            if self.overlay_blink_after_id and self.overlay_win and self.overlay_win.winfo_exists():
                try:
                    self.overlay_win.after_cancel(self.overlay_blink_after_id)
                except Exception:
                    pass
            self.overlay_blink_after_id = None

            # Cancel topmost flash if pending
            if self.overlay_top_flash_after_id and self.overlay_win and self.overlay_win.winfo_exists():
                try:
                    self.overlay_win.after_cancel(self.overlay_top_flash_after_id)
                except Exception:
                    pass
            self.overlay_top_flash_after_id = None

            # Ensure not topmost, then hide
            if self.overlay_win and self.overlay_win.winfo_exists():
                try:
                    self.overlay_win.attributes('-topmost', False)
                except Exception:
                    pass
                self.overlay_win.withdraw()
        except Exception as e:
            self.handle_error("Overlay Hide Error", e, show_message=False)

    def _overlay_blink_tick(self):
        # Toggle green ON/OFF to create a blink effect
        try:
            if not (self.overlay_win and self.overlay_win.winfo_exists() and self.overlay_canvas and self.overlay_dot_id):
                return
            fill_on = "green"
            fill_off = "#2a4f2a"  # dark green-ish
            new_color = fill_on if self.overlay_blink_on else fill_off
            self.overlay_canvas.itemconfig(self.overlay_dot_id, fill=new_color, outline=new_color)
            self.overlay_blink_on = not self.overlay_blink_on
            interval_ms = int(self.config.get("overlay_blink_ms", 500))
            self.overlay_blink_after_id = self.overlay_win.after(interval_ms, self._overlay_blink_tick)
        except Exception:
            # If anything goes wrong, just stop blinking silently
            pass

    # ---------------- Search ----------------

    def perform_search_from_button(self):
        text = self.text_editor.get(1.0, tk.END).strip()
        if not text:
            messagebox.showwarning("No Text", "Please record or type text first.")
            return
        self.perform_search_from_text(text)

    def perform_search_from_text(self, text):
        self.update_status("Initiating web search...")
        self.save_to_history(text, "search")
        threading.Thread(target=self.web_search, args=(text,), daemon=True).start()

    def web_search(self, query):
        browser_closed_event = threading.Event()
        try:
            self.root.after(0, lambda: self.update_status("Launching browser..."))
            with sync_playwright() as p:
                browser = None
                user_path = self.config.get("browser_path")
                if user_path and os.path.exists(user_path):
                    try:
                        browser = p.chromium.launch(executable_path=user_path, headless=False)
                    except Exception:
                        pass
                if not browser:
                    try:
                        browser = p.chromium.launch(channel="brave", headless=False)
                    except Exception:
                        browser = p.chromium.launch(headless=False)
                context = browser.new_context()
                context.on("close", browser_closed_event.set)

                threading.Thread(target=self.browser_timeout_handler, args=(context, browser_closed_event), daemon=True).start()

                page = context.new_page()
                page.goto(f"https://search.brave.com/search?q={query.replace(' ', '+')}")
                self.root.after(0, lambda: self.update_status("Browser is open with search results."))
                browser_closed_event.wait()
        except Exception as e:
            if "browser has been closed" not in str(e).lower():
                self.root.after(0, lambda: self.handle_error("Web Search Error", e))
        finally:
            self.root.after(0, lambda: self.update_status("Browser session ended. Idle."))

    def browser_timeout_handler(self, context, browser_closed_event):
        timeout_minutes = self.config.get("browser_timeout_minutes", 60)
        if timeout_minutes <= 0:
            return
        if timeout_minutes <= 5:
            was_closed_by_user = browser_closed_event.wait(timeout=timeout_minutes * 60)
            if not was_closed_by_user:
                context.close()
            return

        initial_wait_seconds = (timeout_minutes - 5) * 60
        was_closed_by_user = browser_closed_event.wait(timeout=initial_wait_seconds)
        if was_closed_by_user:
            return

        self.root.after(0, lambda: messagebox.showwarning("Browser Timeout", "The browser will close automatically in 5 minutes."))

        final_wait_seconds = 5 * 60
        was_closed_by_user = browser_closed_event.wait(timeout=final_wait_seconds)
        if not was_closed_by_user:
            self.update_status("Browser timeout reached. Closing browser.")
            context.close()

    # ---------------- History ----------------

    def save_to_history(self, text, entry_type):
        if not self.record_history_var.get():
            return
        try:
            with sqlite3.connect(self.history_db) as conn:
                conn.execute("INSERT INTO history (timestamp, text, type) VALUES (?, ?, ?)", (datetime.now().isoformat(), text, entry_type))
        except Exception as e:
            self.handle_error("History Save Error", e, show_message=False)

    def show_history(self):
        try:
            history_window = tk.Toplevel(self.root)
            history_window.title("History")
            history_window.geometry("800x600")

            tree_frame = ttk.Frame(history_window, padding=10)
            tree_frame.pack(fill=tk.BOTH, expand=True)

            columns = ("Timestamp", "Type", "Text")
            tree = ttk.Treeview(tree_frame, columns=columns, show="headings")
            tree.heading("Timestamp", text="Timestamp")
            tree.column("Timestamp", width=150, stretch=tk.NO)
            tree.heading("Type", text="Type")
            tree.column("Type", width=100, stretch=tk.NO)
            tree.heading("Text", text="Text")
            tree.column("Text", width=500)

            vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
            hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
            tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
            vsb.pack(side=tk.RIGHT, fill=tk.Y)
            hsb.pack(side=tk.BOTTOM, fill=tk.X)
            tree.pack(fill=tk.BOTH, expand=True)

            with sqlite3.connect(self.history_db) as conn:
                cursor = conn.execute("SELECT timestamp, type, text FROM history ORDER BY timestamp DESC")
                for row in cursor.fetchall():
                    tree.insert("", "end", values=row)
        except Exception as e:
            self.handle_error("History View Error", e)

    def clear_history(self):
        if messagebox.askyesno("Clear History", "Are you sure you want to permanently delete all history?"):
            try:
                with sqlite3.connect(self.history_db) as conn:
                    conn.execute("DELETE FROM history")
                self.update_status("History cleared")
                self.show_history()
            except Exception as e:
                self.handle_error("Clear History Error", e)

    # ---------------- Settings ----------------

    def change_model(self, event=None):
        self.config["whisper_model"] = self.model_var.get()
        self.save_config()
        self.load_whisper_model()

    def change_language(self, event=None):
        self.config["language"] = self.lang_var.get()
        self.save_config()
        self.update_status(f"Language set to {self.config['language']}")

    def open_settings(self):
        settings_window = tk.Toplevel(self.root)
        settings_window.title("Settings")
        settings_window.geometry("480x600")
        settings_window.transient(self.root)

        main_frame = ttk.Frame(settings_window, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="Dictation Hotkey:").pack(anchor=tk.W, padx=10, pady=(10,0))
        trans_hotkey = ttk.Entry(main_frame)
        trans_hotkey.insert(0, self.config.get("transcription_hotkey"))
        trans_hotkey.pack(fill=tk.X, padx=10, pady=(0,10))

        ttk.Label(main_frame, text="Search Hotkey:").pack(anchor=tk.W, padx=10)
        search_hotkey = ttk.Entry(main_frame)
        search_hotkey.insert(0, self.config.get("search_hotkey"))
        search_hotkey.pack(fill=tk.X, padx=10, pady=(0,10))

        ttk.Label(main_frame, text="Silence Duration (seconds):").pack(anchor=tk.W, padx=10)
        silence_var = tk.IntVar(value=self.config.get("silence_duration"))
        ttk.Spinbox(main_frame, from_=1, to=10, textvariable=silence_var, width=10).pack(anchor=tk.W, padx=10, pady=(0,10))

        ttk.Label(main_frame, text="Browser Executable Path (optional):").pack(anchor=tk.W, padx=10)
        browser_path = ttk.Entry(main_frame)
        browser_path.insert(0, self.config.get("browser_path", ""))
        browser_path.pack(fill=tk.X, padx=10, pady=(0,10))

        ttk.Label(main_frame, text="Browser Timeout (minutes, 0 to disable):").pack(anchor=tk.W, padx=10)
        timeout_var = tk.IntVar(value=self.config.get("browser_timeout_minutes"))
        ttk.Spinbox(main_frame, from_=0, to=1440, textvariable=timeout_var, width=10).pack(anchor=tk.W, padx=10, pady=(0,10))

        ttk.Label(main_frame, text="Hotkey Press Debounce (ms):").pack(anchor=tk.W, padx=10)
        press_db_var = tk.IntVar(value=self.config.get("hotkey_press_debounce_ms", 250))
        ttk.Spinbox(main_frame, from_=0, to=1000, textvariable=press_db_var, width=10).pack(anchor=tk.W, padx=10, pady=(0,10))

        ttk.Label(main_frame, text="Post-Transcription Debounce (ms):").pack(anchor=tk.W, padx=10)
        post_db_var = tk.IntVar(value=self.config.get("hotkey_post_debounce_ms", 150))
        ttk.Spinbox(main_frame, from_=0, to=1000, textvariable=post_db_var, width=10).pack(anchor=tk.W, padx=10, pady=(0,10))

        ttk.Label(main_frame, text="Overlay Blink (ms):").pack(anchor=tk.W, padx=10)
        overlay_blink_var = tk.IntVar(value=self.config.get("overlay_blink_ms", 500))
        ttk.Spinbox(main_frame, from_=100, to=2000, textvariable=overlay_blink_var, width=10).pack(anchor=tk.W, padx=10, pady=(0,10))

        ttk.Label(main_frame, text="Overlay Topmost Flash (ms):").pack(anchor=tk.W, padx=10)
        overlay_flash_var = tk.IntVar(value=self.config.get("overlay_flash_top_ms", 800))
        ttk.Spinbox(main_frame, from_=200, to=3000, textvariable=overlay_flash_var, width=10).pack(anchor=tk.W, padx=10, pady=(0,10))

        # Start minimized toggle
        start_min_var = tk.BooleanVar(value=self.config.get("start_minimized", True))
        ttk.Checkbutton(main_frame, text="Start Minimized (window)", variable=start_min_var).pack(anchor=tk.W, padx=10, pady=(10,0))

        def save_settings():
            self.config.update({
                "transcription_hotkey": trans_hotkey.get(),
                "search_hotkey": search_hotkey.get(),
                "silence_duration": silence_var.get(),
                "browser_path": browser_path.get(),
                "browser_timeout_minutes": timeout_var.get(),
                "hotkey_press_debounce_ms": press_db_var.get(),
                "hotkey_post_debounce_ms": post_db_var.get(),
                "overlay_blink_ms": overlay_blink_var.get(),
                "overlay_flash_top_ms": overlay_flash_var.get(),
                "start_minimized": bool(start_min_var.get())
            })
            self.save_config()
            self.silence_duration = self.config["silence_duration"]
            self.setup_hotkeys()
            # (Re)write launcher to reflect any minimized toggle change
            if sys.platform == "win32":
                self.write_launcher_bat()
            settings_window.destroy()
            self.update_status("Settings saved and hotkeys reloaded")

        ttk.Button(main_frame, text="Save and Close", command=save_settings).pack(pady=20)

    # ---------------- OS Helpers ----------------

    def open_app_folder(self):
        try:
            if sys.platform == "win32":
                os.startfile(self.app_data_dir)
            elif sys.platform == "darwin":
                subprocess.run(["open", str(self.app_data_dir)])
            else:
                subprocess.run(["xdg-open", str(self.app_data_dir)])
        except Exception as e:
            self.handle_error("Open Folder Error", e)

    def start_voice_command_listener(self):
        def listen_for_commands():
            while True:
                if not self.voice_commands_active:
                    time.sleep(1)
                    continue
                try:
                    with sr.Microphone() as source:
                        self.voice_recognizer.adjust_for_ambient_noise(source, duration=0.5)
                        audio = self.voice_recognizer.listen(source, timeout=5, phrase_time_limit=3)
                    command = self.voice_recognizer.recognize_google(audio).lower()
                    if "start dictation" in command:
                        self.root.after(0, self._on_transcription_hotkey)  # respects debounce
                    elif "stop dictation" in command:
                        self.root.after(0, self._on_transcription_hotkey)  # toggle
                    elif "search now" in command:
                        self.root.after(0, self._on_search_hotkey)        # respects debounce
                except (sr.UnknownValueError, sr.RequestError, sr.WaitTimeoutError):
                    continue
                except Exception:
                    time.sleep(2)
        threading.Thread(target=listen_for_commands, daemon=True).start()

    def handle_error(self, context, error, show_message=True):
        error_msg = f"{context}: {str(error)}"
        print(f"[ERROR] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {error_msg}")
        try:
            with open(self.error_log_file, "a") as f:
                f.write(f"[{datetime.now().isoformat()}] {error_msg}\n")
        except Exception:
            pass
        if show_message and hasattr(self, 'root') and self.root.winfo_exists():
            messagebox.showerror(context, error_msg)
        self.update_status(f"Error: {context}")
        # show red LED and briefly debounce to avoid immediate retrigger after errors
        self._set_indicator("error")
        self._overlay_hide()  # hide overlay on errors
        try:
            post_ms = int(self.config.get("hotkey_post_debounce_ms", 150))
        except Exception:
            post_ms = 150
        self.debounce_until = time.monotonic() + (post_ms / 1000.0)

    # ---------------- Startup (Windows) ----------------

    def get_startup_folder(self):
        if sys.platform == "win32":
            return Path(os.getenv("APPDATA")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        elif sys.platform == "darwin":
            return Path.home() / "Library" / "LaunchAgents"
        else:
            return Path.home() / ".config" / "autostart"

    def add_to_startup(self):
        startup_folder = self.get_startup_folder()
        if sys.platform != "win32":
            messagebox.showinfo("Unsupported OS", "Adding to Startup is currently configured for Windows only.")
            return

        try:
            startup_folder.mkdir(parents=True, exist_ok=True)

            # Ensure launcher .bat exists (created/updated based on current settings)
            if not self.launcher_bat.exists():
                ok = self.write_launcher_bat()
                if not ok:
                    return
            else:
                # Keep launcher in sync with current minimized preference
                self.write_launcher_bat()

            shortcut_path = startup_folder / f"{self.app_name}.lnk"

            if shortcut_path.exists():
                messagebox.showinfo("Startup", "Already in startup.")
                return

            try:
                import winshell  # noqa: F401
                from win32com.client import Dispatch
            except Exception:
                self.handle_error("Startup Error", "The 'pywin32' and 'winshell' packages are required to add to startup properly.")
                return

            shell = Dispatch('WScript.Shell')
            shortcut = shell.CreateShortCut(str(shortcut_path))
            shortcut.Targetpath = str(self.launcher_bat)              # .bat path
            shortcut.WorkingDirectory = str(self.script_dir)
            shortcut.IconLocation = sys.executable
            shortcut.WindowStyle = 7 if self.config.get("start_minimized", True) else 1  # 7=min, 1=normal
            shortcut.save()
            messagebox.showinfo("Success", "Added to startup. A launcher (.bat) was created/updated automatically.")
        except Exception as e:
            self.handle_error("Startup Error", e)

    def remove_from_startup(self):
        startup_folder = self.get_startup_folder()
        if sys.platform != "win32":
            messagebox.showinfo("Unsupported OS", "Removing from Startup is currently configured for Windows only.")
            return

        shortcut_path = startup_folder / f"{self.app_name}.lnk"
        try:
            if shortcut_path.exists():
                shortcut_path.unlink()
                messagebox.showinfo("Success", "Removed from startup.")
            else:
                messagebox.showinfo("Startup", "Not found in startup.")
        except Exception as e:
            self.handle_error("Startup Error", e)

    # ---------------- Mainloop ----------------

    def run(self):
        try:
            self.root.mainloop()
        except Exception as e:
            self.handle_error("Application Runtime Error", e)
        finally:
            keyboard.unhook_all()


if __name__ == "__main__":
    app = VoiceFlowAssistant()
    app.run()
