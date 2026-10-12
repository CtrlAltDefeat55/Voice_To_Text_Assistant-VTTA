#!/bin/bash
# ==========================================================================
#  VTTA - Voice to Text Assistant : macOS / Linux launcher
#  macOS: double-click "Start VTTA.command" (it runs this script).
#  Linux: run ./start-vtta.sh
#
#  The first run sets up a private Python environment in the ".venv" folder
#  next to this file (needs internet once, takes a few minutes). Later runs
#  start VTTA straight away. Everything stays inside this folder.
#
#    ./start-vtta.sh debug   - run in this terminal to see errors
#    ./start-vtta.sh setup   - repeat the setup (repair)
# ==========================================================================
HERE="$(cd "$(dirname "$0")" && pwd)" || exit 1
# Release downloads keep the app in an "app" subfolder; a source checkout keeps it next to this script.
if [ -f "$HERE/app/Voice_To_Text_Assistant.py" ]; then APP_DIR="$HERE/app"; else APP_DIR="$HERE"; fi
cd "$APP_DIR" || exit 1
VENV="$APP_DIR/.venv"
VPY="$VENV/bin/python"
REQ="$APP_DIR/requirements.txt"
STAMP="$VENV/vtta-requirements.txt"
OS="$(uname -s)"

say() { printf '  %s\n' "$*"; }

pause_exit() {
    if [ -t 0 ]; then read -r -p "  Press Return to close. " _; fi
    exit "${1:-1}"
}

fail() {
    echo
    say "Setup did not finish: $*"
    say "The messages above say why. Fix it, then start VTTA again."
    pause_exit 1
}

venv_ok() { [ -x "$VPY" ] && "$VPY" -c 'import tkinter' >/dev/null 2>&1; }

find_python() {
    # Prefer versions that all of VTTA's components support, and only ones that include Tk.
    local candidates=()
    if [ "$OS" = "Darwin" ]; then
        for v in 3.12 3.11 3.13 3.10; do
            candidates+=("/Library/Frameworks/Python.framework/Versions/$v/bin/python3"
                         "/opt/homebrew/bin/python$v" "/usr/local/bin/python$v")
        done
    fi
    candidates+=(python3.12 python3.11 python3.13 python3.10 python3)
    for p in "${candidates[@]}"; do
        if command -v "$p" >/dev/null 2>&1 &&
           "$p" -c 'import sys, tkinter, venv; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
            command -v "$p"
            return 0
        fi
    done
    return 1
}

launch() {
    if [ "$1" = "debug" ]; then
        exec "$VPY" "$APP_DIR/Voice_To_Text_Assistant.py"
    fi
    nohup "$VPY" "$APP_DIR/Voice_To_Text_Assistant.py" >/dev/null 2>&1 &
    disown 2>/dev/null
    say "VTTA is starting. You can close this window."
    exit 0
}

setup() {
    echo
    say "VTTA first-time setup"
    say "---------------------"
    say "This creates a private Python environment inside this folder and"
    say "downloads the components VTTA needs (about 1 GB, one time only)."
    echo
    if ! venv_ok; then
        PY="$(find_python)" || {
            say "Python 3.10 or newer (with Tk) was not found."
            if [ "$OS" = "Darwin" ]; then
                say "Install Python 3.12 from python.org (the macOS installer), then start VTTA again."
                open "https://www.python.org/downloads/macos/" 2>/dev/null
            else
                say "Install it with your package manager, e.g.:"
                say "  sudo apt install python3 python3-venv python3-tk python3-dev portaudio19-dev"
            fi
            pause_exit 1
        }
        rm -rf "$VENV"
        say "Creating the environment with: $PY"
        "$PY" -m venv "$VENV" || fail "could not create the Python environment"
    fi

    # PyAudio is compiled against PortAudio on macOS/Linux.
    if ! "$VPY" -c 'import pyaudio' >/dev/null 2>&1; then
        if [ "$OS" = "Darwin" ]; then
            if command -v brew >/dev/null 2>&1; then
                if ! brew list portaudio >/dev/null 2>&1; then
                    say "Installing PortAudio (audio library) with Homebrew..."
                    brew install portaudio || fail "brew install portaudio failed"
                fi
                BREW_PREFIX="$(brew --prefix)"
                export CFLAGS="-I$BREW_PREFIX/include $CFLAGS" LDFLAGS="-L$BREW_PREFIX/lib $LDFLAGS"
            else
                say "Note: the audio component needs PortAudio. If the next step fails, install"
                say "Homebrew from https://brew.sh, run 'brew install portaudio', then start VTTA again."
            fi
        elif [ ! -e /usr/include/portaudio.h ] && [ ! -e /usr/local/include/portaudio.h ]; then
            say "Note: the audio component needs PortAudio. If the next step fails, run:"
            say "  sudo apt install portaudio19-dev python3-dev"
        fi
    fi

    echo
    say "Installing components. This can take several minutes..."
    echo
    "$VPY" -m pip install --disable-pip-version-check --upgrade pip
    if [ "$OS" = "Linux" ] && ! "$VPY" -c 'import torch' >/dev/null 2>&1; then
        # The default Linux PyTorch includes GPU libraries (several GB); the CPU build is much smaller.
        "$VPY" -m pip install --disable-pip-version-check torch --index-url https://download.pytorch.org/whl/cpu \
            || say "CPU-only PyTorch unavailable; using the standard package instead."
    fi
    "$VPY" -m pip install --disable-pip-version-check -r "$REQ" || fail "installing components failed"
    "$VPY" -c 'import tkinter, numpy, PIL, pyaudio, whisper' || fail "a component could not be loaded"
    cp "$REQ" "$STAMP"
    echo
    say "Setup complete."
}

# In a full source download on macOS, hide the launchers Finder users don't need.
if [ "$OS" = "Darwin" ] && [ -f "$HERE/Start VTTA.bat" ]; then
    chflags hidden "$HERE/Start VTTA.bat" "$HERE/start-vtta.sh" 2>/dev/null
fi

if [ "$1" = "setup" ] || ! venv_ok; then
    setup
elif ! cmp -s "$REQ" "$STAMP"; then
    setup   # requirements changed (app updated): install what's new
fi
launch "$1"
