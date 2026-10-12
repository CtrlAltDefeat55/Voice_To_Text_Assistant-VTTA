# Security Policy

## Support & updates
This is a small hobby project. Security updates are **best‑effort**.

## Reporting a vulnerability
Open an issue and include the word **SECURITY** in the title. Avoid posting sensitive proof‑of‑concept details publicly; keep reports minimal.

## Risk & behavior disclaimers
This app:
- **Records audio** from your microphone during dictation (hotkey/button).
- Can **paste to your active window** using clipboard/keyboard automation (optional).
- Can **open a web browser** to a search page (a private Playwright window or your default browser) when requested.
- Can **store transcription history locally** in SQLite if you enable it.
- Keeps audio **in memory only**; it does not write recordings or log files to disk.
- **Voice command keywords** (“start dictation”, “stop dictation”, “search now”) use Google’s **online** recognizer **only when** you enable **Enable Voice Commands** in Settings. This is **OFF by default**.

Recommendations:
- Keep “Save to history” **off** if you don’t want local text saved.
- Verify the active window before relying on “Paste into the active app after hotkey dictation.”
- Review OS microphone & accessibility permissions.
- Prefer **offline-only** usage by keeping **Enable Voice Commands** **off**.


**Use at your own risk.** The maintainers are **not responsible** for any data loss or damage.


